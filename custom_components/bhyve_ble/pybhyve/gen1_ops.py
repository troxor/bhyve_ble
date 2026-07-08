"""Gen1 session orchestration."""

from __future__ import annotations

import asyncio
import base64
import binascii
import secrets
import struct

from .gen1_codec import (
    GEN1_DEFAULT_TIMESTAMP_TAIL,
    build_gen1_manual_start_payload,
    build_gen1_stop_payload,
    encode_gen1_plaintext,
    gen1_mesh_attach_plaintexts,
    gen1_onboard_write_plaintexts,
)
from .gen1_session import Gen1Session, run_gen1_session

# Client-generated mesh id for first-time onboard (Hermes #22427: 1..65533).
_GEN1_DEVICE_ID_MIN = 1
_GEN1_DEVICE_ID_MAX = 65533

# Pause after a manual-start write before checking whether watering status arrived.
GEN1_START_CONFIRM_DELAY_S = 0.5


class Gen1PairingError(Exception):
    """Gen 1 pairing failed."""


def gen1_device_id(device_id: int | None = None) -> int:
    """Validate a proposed mesh id or pick a random unused value in 1..65533."""
    if device_id is not None:
        mid = int(device_id)
        if not _GEN1_DEVICE_ID_MIN <= mid <= _GEN1_DEVICE_ID_MAX:
            msg = (
                f"device_id must be in [{_GEN1_DEVICE_ID_MIN}, {_GEN1_DEVICE_ID_MAX}], got {mid}"
            )
            raise Gen1PairingError(msg)
        return mid
    span = _GEN1_DEVICE_ID_MAX - _GEN1_DEVICE_ID_MIN + 1
    return _GEN1_DEVICE_ID_MIN + secrets.randbelow(span)


def gen1_network_key(user_input: str | None = None) -> bytes:
    """
    Parse a 16-byte network key from user input.

    Accepts standard Base64 (Orbit web UI) or exactly 32 hex characters.
    None or blank input returns a freshly generated random key.
    """
    if user_input is None:
        return secrets.token_bytes(16)
    s = user_input.strip()
    if not s:
        return secrets.token_bytes(16)

    if len(s) == 32 and all(c in "0123456789abcdefABCDEF" for c in s):
        return binascii.unhexlify(s)

    try:
        raw = base64.b64decode(s, validate=True)
    except binascii.Error as e:
        msg = "Invalid network key: use Base64 or raw hex (32 characters) only"
        raise ValueError(msg) from e
    if len(raw) != 16:
        msg = "Invalid network key: value must decode to 16 bytes"
        raise ValueError(msg)
    return raw


async def run_gen1_pairing(
    gen1: Gen1Session,
    device_id: int | None = None,
    *,
    tail: bytes = GEN1_DEFAULT_TIMESTAMP_TAIL,
) -> tuple[int, int | None]:
    """
    First-time onboard wire script.

    When device_id is None a random mesh id in 1..65533 is proposed in the 0x84
    register; the device confirms its assigned BLE Device ID in the 0xc4 NOTIFY.
    Returns (proposed_id, assigned_id).
    """
    proposed = gen1_device_id(device_id)
    await run_gen1_session(gen1, gen1_onboard_write_plaintexts(proposed, tail=tail))
    return proposed, gen1.assigned_device_id


def _gen1_cmd(device_id: int, cmd: int, inner: bytes = b"") -> bytes:
    return encode_gen1_plaintext(struct.pack("<H", device_id), cmd, inner)


async def run_gen1_passive_poll(gen1: Gen1Session, device_id: int) -> None:
    cmd = gen1.alloc_cmd()
    pt = _gen1_cmd(device_id, cmd, bytes.fromhex("024000"))
    await gen1.send_and_wait(f"gen1 passive poll cmd=0x{cmd:02x}", pt)


async def run_gen1_status_session(
    gen1: Gen1Session,
    device_id: int,
    *,
    tail: bytes = GEN1_DEFAULT_TIMESTAMP_TAIL,
    passive_poll: bool = True,
) -> dict:
    await run_gen1_session(
        gen1,
        gen1_mesh_attach_plaintexts(
            device_id, label_prefix="gen1 status", include_85_clear=False, tail=tail
        ),
    )
    if passive_poll and not (
        "watering_status" in gen1.status_snapshot or "watering_idle" in gen1.status_snapshot
    ):
        await run_gen1_passive_poll(gen1, device_id)
    return gen1.status_snapshot


async def run_gen1_manual_start(
    gen1: Gen1Session,
    device_id: int,
    duration_sec: int,
    *,
    tail: bytes = GEN1_DEFAULT_TIMESTAMP_TAIL,
    reconnect: bool = True,
    confirm_delay_s: float = GEN1_START_CONFIRM_DELAY_S,
) -> dict:
    if reconnect:
        await run_gen1_session(
            gen1,
            gen1_mesh_attach_plaintexts(
                device_id, label_prefix="gen1 reconnect", include_85_clear=True, tail=tail
            ),
        )
    cmd = gen1.alloc_cmd()
    pt = _gen1_cmd(device_id, cmd, build_gen1_manual_start_payload(duration_sec))
    await gen1.send_only(f"gen1 manual start {duration_sec}s cmd=0x{cmd:02x}", pt)
    if confirm_delay_s > 0:
        await asyncio.sleep(confirm_delay_s)
    if not (
        "watering_status" in gen1.status_snapshot or "watering_idle" in gen1.status_snapshot
    ):
        await run_gen1_passive_poll(gen1, device_id)
    return gen1.status_snapshot


async def run_gen1_stop_sequence(
    gen1: Gen1Session,
    device_id: int,
) -> None:
    cmd = gen1.alloc_cmd()
    await gen1.send_and_wait(
        f"gen1 stop: status probe cmd=0x{cmd:02x}",
        _gen1_cmd(device_id, cmd, bytes.fromhex("0d4002000000")),
    )
    for repeat in (1, 2):
        await gen1.send_and_wait(
            f"gen1 stop cmd=0x{cmd:02x} ({repeat}/2)",
            _gen1_cmd(device_id, cmd, build_gen1_stop_payload()),
        )
    cmd2 = gen1.alloc_cmd()
    await gen1.send_and_wait(
        f"gen1 stop cmd=0x{cmd2:02x}",
        _gen1_cmd(device_id, cmd2, build_gen1_stop_payload()),
    )


async def run_gen1_stop_watering(
    gen1: Gen1Session,
    device_id: int,
    *,
    tail: bytes = GEN1_DEFAULT_TIMESTAMP_TAIL,
) -> dict:
    await run_gen1_status_session(gen1, device_id, tail=tail, passive_poll=False)
    await run_gen1_stop_sequence(gen1, device_id)
    await run_gen1_passive_poll(gen1, device_id)
    return gen1.status_snapshot


async def run_gen1_onboard(
    gen1: Gen1Session,
    device_id: int | None = None,
    *,
    tail: bytes = GEN1_DEFAULT_TIMESTAMP_TAIL,
) -> tuple[int | None, dict]:
    proposed, assigned = await run_gen1_pairing(gen1, device_id, tail=tail)
    status_device_id = assigned if assigned is not None else proposed
    await run_gen1_status_session(
        gen1, status_device_id, tail=tail, passive_poll=False
    )
    return assigned if assigned is not None else gen1.assigned_device_id, gen1.status_snapshot


__all__ = [
    "GEN1_START_CONFIRM_DELAY_S",
    "Gen1PairingError",
    "gen1_device_id",
    "gen1_network_key",
    "run_gen1_manual_start",
    "run_gen1_onboard",
    "run_gen1_pairing",
    "run_gen1_status_session",
    "run_gen1_stop_watering",
]
