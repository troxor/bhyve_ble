"""
Gen1 codec/session regression tests.

Fixed hex plaintext vectors from lab Gen1 onboard traces (device id ``0x827c``,
timestamps ``0x6a31ffea`` / ``0x6a31ffed``).
"""

from __future__ import annotations

import asyncio
import struct

import pytest
from pybhyve.gen1_codec import (
    assigned_device_id_from_register_response,
    build_gen1_manual_start_payload,
    build_gen1_stop_payload,
    decode_gen1_inner_payload,
    decode_gen1_plaintext,
    device_id_from_plaintext,
    encode_gen1_plaintext,
    gen1_mesh_attach_plaintexts,
    gen1_onboard_write_plaintexts,
)
from pybhyve.gen1_ops import Gen1PairingError, gen1_device_id, run_gen1_pairing
from pybhyve.gen1_session import Gen1Session

DEVICE_ID = 0x827C
PING_TS = 0x6A31FFEA  # unix ts for ping (0x81) in test vectors

# Decrypted host->device writes from a lab Gen1 onboard trace.
ONBOARD_APP_WRITES = [
    "7c82810540eaff316ad4fe",
    "7c82020140000000",
    "7c8203034000000000000000",
    "7c82840040017c8200000000",
    "7c82050140000000",
    "7c82860540edff316ad4fe",
]

# Decrypted device->host frames from the same trace.
REGISTER_RESPONSE = "7c82c40040017c8200000000"  # 0x84 -> 0xc4, assigns mesh 0x827c
IDLE_PUSH = "7c82b1024001ffffffff0000"  # 0xb1 async push: watering idle
IDLE_ACK = "7c82f1024001ffffffff00"  # client ACK of 0xb1 (cmd|0x40, payload trimmed 1 B)


def test_onboard_writes_match_vectors() -> None:
    """The onboard script reproduces the expected decrypted plaintexts."""
    steps = gen1_onboard_write_plaintexts(DEVICE_ID, now=PING_TS)
    produced = [pt.hex() for _label, pt in steps]
    assert produced == ONBOARD_APP_WRITES


def test_gen1_network_char_prefix_equals_session_magic() -> None:
    """
    Gen 1 ``network_char`` prefix MUST equal the per-frame magic (device id).

    Example: mesh ``0x8088`` -> prefix ``8880``; every gen1 frame uses the same LE16 magic.
    Provisioning with the gen2 ``01 00`` default while using a different session magic
    makes the timer drop all commands (no NOTIFY).
    """
    from pybhyve.link_crypto import build_network_char_payload

    key = bytes.fromhex("ce54f98ee55a133295cf6d0a2f97db6b")
    mesh = 0x8088
    prov = build_network_char_payload(key, mesh)
    assert prov[0:2] == struct.pack("<H", mesh) == bytes.fromhex("8880")
    # And the magic on the wire (first 2 plaintext bytes) uses the identical LE16.
    onboard = gen1_onboard_write_plaintexts(mesh, now=PING_TS)
    assert all(pt[0:2] == prov[0:2] for _label, pt in onboard)


def test_decode_register_response_and_assigned_device_id() -> None:
    dec = decode_gen1_plaintext(bytes.fromhex(REGISTER_RESPONSE))
    assert dec["cmd"] == 0xC4
    assert dec["request_cmd"] == 0x84
    assert dec["direction"] == "device"
    assert assigned_device_id_from_register_response(dec) == DEVICE_ID
    assert device_id_from_plaintext(bytes.fromhex(REGISTER_RESPONSE)) == DEVICE_ID


def test_idle_push_decodes_and_ack() -> None:
    pt = bytes.fromhex(IDLE_PUSH)
    dec = decode_gen1_plaintext(pt)
    inner = decode_gen1_inner_payload(bytes.fromhex(dec["payload_hex"]))
    assert inner == {"kind": "watering_idle", "active": False}
    ack_cmd = int(dec["cmd"]) | 0x40
    ack_payload = bytes.fromhex(dec["payload_hex"])[:-1]
    ack = encode_gen1_plaintext(struct.pack("<H", DEVICE_ID), ack_cmd, ack_payload)
    assert ack.hex() == IDLE_ACK


def test_manual_start_and_stop_inner_records() -> None:
    # 60 s manual run inner record from gen1_pairingY-activity1.
    assert build_gen1_manual_start_payload(60).hex() == "0d40043c0000000000"
    # Default user stop (reason 1000, code 0x02) from gen1_start-stop1.
    assert build_gen1_stop_payload().hex() == "0e40e8030200"


def test_watering_status_remaining_quarters() -> None:
    # 02 40 04 <flags> <rem_quarters> 40 <total LE16> 00 ; remaining = byte4 * 4 s.
    inner = decode_gen1_inner_payload(bytes.fromhex("024004000a40b00400"))
    assert inner is not None
    assert inner["kind"] == "watering_status"
    assert inner["active"] is True
    assert inner["remaining_sec"] == 0x0A * 4
    assert inner["total_sec"] == 0x04B0  # 1200


def test_status_session_omits_clear_step() -> None:
    """Status session must not send the 0x85 clear (cancels an active run)."""
    cmds = [
        pt
        for _l, pt in gen1_mesh_attach_plaintexts(
            DEVICE_ID, label_prefix="gen1 status", include_85_clear=False, now=PING_TS
        )
    ]
    clear_inner = bytes.fromhex("004000000000000000")
    assert all(clear_inner not in pt for pt in cmds)


def test_gen1_device_id_in_range_and_validates() -> None:
    for _ in range(200):
        mid = gen1_device_id()
        assert 1 <= mid <= 65533
    assert gen1_device_id(DEVICE_ID) == DEVICE_ID
    with pytest.raises(Gen1PairingError):
        gen1_device_id(0)
    with pytest.raises(Gen1PairingError):
        gen1_device_id(0xFFFF)


class _FakeSender:
    """Collects plaintexts and lets the test feed device NOTIFYs back to the session."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes]] = []
        self.session: Gen1Session | None = None

    async def send(self, pt: bytes, label: str) -> None:
        self.sent.append((label, pt))
        # Echo a device response for request cmds so send_and_wait does not time out.
        if self.session is not None and len(pt) >= 3 and pt[2] < 0xC0 and pt[2] != 0x86:
            resp = pt[0:2] + bytes([pt[2] | 0x40]) + pt[3:]
            self.session.on_notify_plaintext(resp)


def test_session_tracks_assigned_device_id_and_runs_onboard() -> None:
    async def scenario() -> tuple[int | None, set[int], int | None, int]:
        sender = _FakeSender()
        session = Gen1Session(
            magic=struct.pack("<H", DEVICE_ID),
            send_plaintext=sender.send,
            step_delay_s=0.0,
            ack_delay_s=0.0,
            response_timeout_s=1.0,
        )
        sender.session = session
        proposed, assigned = await run_gen1_pairing(session, DEVICE_ID)
        sent_cmds = {pt[2] for _l, pt in sender.sent}
        return assigned, sent_cmds, session.assigned_device_id, proposed

    assigned, sent_cmds, tracked, proposed = asyncio.run(scenario())
    # Onboard sent the six application writes 0x81, 0x02, 0x03, 0x84, 0x05, 0x86.
    assert {0x81, 0x02, 0x03, 0x84, 0x05, 0x86} <= sent_cmds
    # The echoed 0xc4 register response carried the proposed device id.
    assert assigned == DEVICE_ID
    assert tracked == DEVICE_ID
    assert proposed == DEVICE_ID


def test_run_gen1_pairing_generates_when_device_id_omitted() -> None:
    async def scenario() -> int:
        sender = _FakeSender()
        session = Gen1Session(
            magic=b"\x00\x00",
            send_plaintext=sender.send,
            step_delay_s=0.0,
            ack_delay_s=0.0,
            response_timeout_s=1.0,
        )
        sender.session = session
        proposed, _assigned = await run_gen1_pairing(session, None)
        return proposed

    proposed = asyncio.run(scenario())
    assert 1 <= proposed <= 65533


def _build_session(sender: _FakeSender) -> Gen1Session:
    session = Gen1Session(
        magic=struct.pack("<H", DEVICE_ID),
        send_plaintext=sender.send,
        step_delay_s=0.0,
        ack_delay_s=0.0,
        response_timeout_s=1.0,
    )
    sender.session = session
    return session


def test_status_session_op_sends_attach_without_clear() -> None:
    from pybhyve.gen1_ops import run_gen1_status_session

    async def scenario() -> list[bytes]:
        sender = _FakeSender()
        session = _build_session(sender)
        await run_gen1_status_session(session, DEVICE_ID, passive_poll=False)
        return [pt for _l, pt in sender.sent]

    sent = asyncio.run(scenario())
    cmds = {pt[2] for pt in sent}
    # Reconnect/status attach handshake (0x81..0x86), never the standalone register cmd 0x84.
    assert {0x81, 0x02, 0x03, 0x04, 0x85, 0x86} <= cmds
    assert 0x84 not in cmds
    # The 0x85 clear (cancels an active run) must not be sent during a status read.
    clear_inner = bytes.fromhex("004000000000000000")
    assert all(clear_inner not in pt for pt in sent)


def test_stop_sequence_op_sends_probe_then_three_stops() -> None:
    from pybhyve.gen1_ops import run_gen1_stop_sequence

    async def scenario() -> list[bytes]:
        sender = _FakeSender()
        session = _build_session(sender)
        await run_gen1_stop_sequence(session, DEVICE_ID)
        return [pt for _l, pt in sender.sent]

    sent = asyncio.run(scenario())
    probes = [pt for pt in sent if pt[3:].hex() == "0d4002000000"]
    stops = [pt for pt in sent if pt[3:].hex() == "0e40e8030200"]
    assert len(probes) == 1
    assert len(stops) == 3
    # Two stops reuse one session cmd, the third uses the next (N, N, N+1).
    stop_cmds = [pt[2] for pt in stops]
    assert stop_cmds[0] == stop_cmds[1]
    assert stop_cmds[2] == stop_cmds[0] + 1


def test_manual_start_op_sends_run_record() -> None:
    from pybhyve.gen1_ops import run_gen1_manual_start

    async def scenario() -> list[bytes]:
        sender = _FakeSender()
        session = _build_session(sender)
        await run_gen1_manual_start(
            session, DEVICE_ID, 60, reconnect=False, confirm_delay_s=0.0
        )
        return [pt for _l, pt in sender.sent]

    sent = asyncio.run(scenario())
    starts = [pt for pt in sent if pt[3:].hex() == "0d40043c0000000000"]
    assert starts, "expected a 60 s manual-start inner record (0d40043c0000000000)"
