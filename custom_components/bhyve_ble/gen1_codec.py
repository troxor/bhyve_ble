"""Gen1 hose-timer application plaintext (legacy command protocol, not Orbit protobuf)."""

from __future__ import annotations

import struct
import time
from typing import Any

# Same bounds as orbit_codec (app UI: 15 s .. 4 h).
MANUAL_RUN_SEC_MIN = 15
MANUAL_RUN_SEC_MAX = 4 * 3600

GEN1_RESPONSE_BIT = 0x40

# Trailing 2 bytes on 0x81 / 0x86 timestamp payloads (stable on captured BH1G1 units).
GEN1_DEFAULT_TIMESTAMP_TAIL = bytes([0xD4, 0xFE])

# Device async notifies (app ACK = cmd | 0x40). Onboard/reconnect often 0xa4-0xac;
# during manual watering also 0x87-0x8c (status/fault); faults 0x88.
GEN1_ASYNC_NOTIFY_CMDS = (
    frozenset(range(0xA4, 0xAD))
    | frozenset(range(0xB1, 0xC0))
    | frozenset(range(0x87, 0x8D))
)

# First application cmd after reconnect/onboard handshake (0x81-0x86) in gen1_pairingY-activity1.
GEN1_POST_HANDSHAKE_CMD = 0x87

# After commit (0x86): wait for async NOTIFYs to go quiet.
GEN1_COMMIT_DRAIN_MAX_S = 2.0
GEN1_COMMIT_QUIET_S = 0.35

# Inner record marker (also GEN1_RESPONSE_BIT on the outer cmd byte).
GEN1_INNER_RECORD_MARKER = 0x40

# Stop payload LE u16 reason (1000) and trailing code byte from gen1_pairingY-activity1.
GEN1_DEFAULT_STOP_REASON = 1000
GEN1_DEFAULT_STOP_CODE = 0x02

# Only gen1 hose-timer SKU (1 port); not on the BLE device_info wire.
GEN1_MODEL = "HT25"


# Hose-timer pack mV → percent (same linear map as gen2 / HA integration).
BATTERY_MV_EMPTY = 2400
BATTERY_MV_FULL = 3000


def estimate_battery_percent_from_mv(mv: int) -> int:
    """Linear clamp to 0-100 between ``BATTERY_MV_EMPTY`` and ``BATTERY_MV_FULL``."""
    low = min(BATTERY_MV_EMPTY, BATTERY_MV_FULL)
    high = max(BATTERY_MV_EMPTY, BATTERY_MV_FULL)
    if high <= low:
        return 0
    clamped = max(low, min(int(mv), high))
    return int((clamped - low) * 100 / (high - low))


def mesh_prefix_bytes(mesh_device_id: int) -> bytes:
    """2-byte session magic (LE uint16 mesh / BLE device id)."""
    mid = int(mesh_device_id)
    if not 0 <= mid <= 0xFFFF:
        msg = "mesh_device_id must be 0..65535"
        raise ValueError(msg)
    return struct.pack("<H", mid)


def _mesh_register_payload(magic: bytes) -> bytes:
    return bytes([0x00, 0x40, 0x01]) + magic + bytes(4)


def encode_gen1_plaintext(magic: bytes, cmd: int, payload: bytes = b"") -> bytes:
    """Build ``[magic:2][cmd:1][payload…]``."""
    if len(magic) != 2:
        msg = "magic must be 2 bytes"
        raise ValueError(msg)
    c = int(cmd) & 0xFF
    return magic + bytes([c]) + payload


def build_gen1_timestamp_payload(
    *,
    tail: bytes = GEN1_DEFAULT_TIMESTAMP_TAIL,
    now: int | None = None,
) -> bytes:
    """
    Payload for gen1 cmds **0x81** and **0x86**: ``05 40`` + uint32 LE unix + 2-byte tail.

    Matches ``gen1_pairingX.pcapng`` / ``gen1_pairingX-reconn1.pcapng``.
    """
    if len(tail) != 2:
        msg = "tail must be 2 bytes"
        raise ValueError(msg)
    ts = int(time.time() if now is None else now)
    return bytes([0x05, 0x40]) + struct.pack("<I", ts) + tail


def encode_gen1_ack(magic: bytes, notify_cmd: int, notify_payload: bytes) -> bytes:
    """ACK an async device notify (``0xb1`` → ``0xf1``, payload trimmed by 1 B per capture)."""
    ack_cmd = int(notify_cmd) | GEN1_RESPONSE_BIT
    pl = notify_payload[:-1] if notify_payload else notify_payload
    return encode_gen1_plaintext(magic, ack_cmd, pl)


def _gen1_timestamp_pair(
    *,
    tail: bytes = GEN1_DEFAULT_TIMESTAMP_TAIL,
    now: int | None = None,
) -> tuple[bytes, bytes]:
    """0x81 and 0x86 use separate unix timestamps (~3 s apart in captures)."""
    if now is None:
        return (
            build_gen1_timestamp_payload(tail=tail),
            build_gen1_timestamp_payload(tail=tail),
        )
    return (
        build_gen1_timestamp_payload(tail=tail, now=now),
        build_gen1_timestamp_payload(tail=tail, now=now + 3),
    )


def gen1_onboard_write_plaintexts(
    mesh_device_id: int,
    *,
    tail: bytes = GEN1_DEFAULT_TIMESTAMP_TAIL,
    now: int | None = None,
) -> list[tuple[str, bytes]]:
    """
    App→device plaintext sequence from ``gen1_pairingX.pcapng`` (after link handshake).

    Returns ``(label, plaintext)`` pairs. Caller handles 0x86 async notifies + fw test ``0xb4``.
    """
    magic = mesh_prefix_bytes(mesh_device_id)
    ts_ping, ts_commit = _gen1_timestamp_pair(tail=tail, now=now)
    reg = _mesh_register_payload(magic)
    return [
        ("gen1 onboard: ping 0x81", encode_gen1_plaintext(magic, 0x81, ts_ping)),
        ("gen1 onboard: 0x02", encode_gen1_plaintext(magic, 0x02, bytes.fromhex("0140000000"))),
        ("gen1 onboard: 0x03", encode_gen1_plaintext(magic, 0x03, bytes.fromhex("034000000000000000"))),
        ("gen1 onboard: register mesh 0x84", encode_gen1_plaintext(magic, 0x84, reg)),
        ("gen1 onboard: 0x05", encode_gen1_plaintext(magic, 0x05, bytes.fromhex("0140000000"))),
        ("gen1 onboard: commit 0x86", encode_gen1_plaintext(magic, 0x86, ts_commit)),
    ]


def gen1_reconnect_write_plaintexts(
    mesh_device_id: int,
    *,
    tail: bytes = GEN1_DEFAULT_TIMESTAMP_TAIL,
    now: int | None = None,
) -> list[tuple[str, bytes]]:
    """App→device plaintext sequence from ``gen1_pairingX-reconn1.pcapng``."""
    magic = mesh_prefix_bytes(mesh_device_id)
    ts_ping, ts_commit = _gen1_timestamp_pair(tail=tail, now=now)
    reg = _mesh_register_payload(magic)
    return [
        ("gen1 reconnect: ping 0x81", encode_gen1_plaintext(magic, 0x81, ts_ping)),
        ("gen1 reconnect: poll 0x02", encode_gen1_plaintext(magic, 0x02, bytes.fromhex("024000"))),
        ("gen1 reconnect: 0x03", encode_gen1_plaintext(magic, 0x03, bytes.fromhex("034000000000000000"))),
        ("gen1 reconnect: 0x04", encode_gen1_plaintext(magic, 0x04, bytes.fromhex("0140000000"))),
        ("gen1 reconnect: 0x85 register", encode_gen1_plaintext(magic, 0x85, reg)),
        ("gen1 reconnect: 0x85 clear", encode_gen1_plaintext(magic, 0x85, bytes.fromhex("004000000000000000"))),
        ("gen1 reconnect: 0x85 tail", encode_gen1_plaintext(magic, 0x85, bytes.fromhex("094000"))),
        ("gen1 reconnect: commit 0x86", encode_gen1_plaintext(magic, 0x86, ts_commit)),
    ]


def gen1_status_session_plaintexts(
    mesh_device_id: int,
    *,
    tail: bytes = GEN1_DEFAULT_TIMESTAMP_TAIL,
    now: int | None = None,
) -> list[tuple[str, bytes]]:
    """
    Read-only mesh attach for ``--status`` on a new BLE link.

    Same as :func:`gen1_reconnect_write_plaintexts` except the ``0x85 clear``
    (``004000000000000000``) step is omitted so an in-progress manual run is not
    cancelled.  The ``0x02`` poll NOTIFY carries ``watering_status`` when active.

    Do **not** send ``0d4002000000`` here — that inner record is the stop-sequence
    preamble in ``gen1_start-stop1.pcapng``, not a passive status read.
    """
    magic = mesh_prefix_bytes(mesh_device_id)
    ts_ping, ts_commit = _gen1_timestamp_pair(tail=tail, now=now)
    reg = _mesh_register_payload(magic)
    return [
        ("gen1 status: ping 0x81", encode_gen1_plaintext(magic, 0x81, ts_ping)),
        ("gen1 status: poll 0x02", encode_gen1_plaintext(magic, 0x02, bytes.fromhex("024000"))),
        ("gen1 status: 0x03", encode_gen1_plaintext(magic, 0x03, bytes.fromhex("034000000000000000"))),
        ("gen1 status: device_info 0x04", encode_gen1_plaintext(magic, 0x04, bytes.fromhex("0140000000"))),
        ("gen1 status: 0x85 register", encode_gen1_plaintext(magic, 0x85, reg)),
        ("gen1 status: 0x85 tail", encode_gen1_plaintext(magic, 0x85, bytes.fromhex("094000"))),
        ("gen1 status: commit 0x86", encode_gen1_plaintext(magic, 0x86, ts_commit)),
    ]


def gen1_session_poll_plaintext(mesh_device_id: int, cmd: int) -> bytes:
    """Passive ``024000`` poll on a session cmd (``gen1-idle-with-disconnect.pcapng``)."""
    magic = mesh_prefix_bytes(mesh_device_id)
    return encode_gen1_plaintext(magic, cmd, bytes.fromhex("024000"))


def build_gen1_manual_start_payload(duration_sec: int) -> bytes:
    """
    Inner record for manual run start: ``0d 40 04`` + uint32 LE seconds + ``00 00`` pad.

    From ``gen1_pairingY-activity1`` (60 s → ``0d40043c0000000000``).
    """
    sec = int(duration_sec)
    if not MANUAL_RUN_SEC_MIN <= sec <= MANUAL_RUN_SEC_MAX:
        msg = f"duration_sec must be in [{MANUAL_RUN_SEC_MIN}, {MANUAL_RUN_SEC_MAX}], got {sec}"
        raise ValueError(msg)
    return (
        bytes([0x0D, GEN1_INNER_RECORD_MARKER, 0x04])
        + struct.pack("<I", sec)
        + bytes([0x00, 0x00])
    )


def build_gen1_stop_payload(
    *,
    reason: int = GEN1_DEFAULT_STOP_REASON,
    code: int = GEN1_DEFAULT_STOP_CODE,
) -> bytes:
    """Inner record for user stop: ``0e 40 e8 03 02 00`` when reason=1000, code=0x02."""
    r = int(reason)
    if not 0 <= r <= 0xFFFF:
        msg = "reason must be 0..65535"
        raise ValueError(msg)
    c = int(code) & 0xFF
    return bytes([0x0E, GEN1_INNER_RECORD_MARKER]) + struct.pack("<HBB", r, c, 0)


def build_gen1_status_probe_payload() -> bytes:
    """Stop-preamble inner ``0d4002000000`` (``gen1_start-stop1`` before ``0e40…``); not for ``--status``."""
    return bytes.fromhex("0d4002000000")


def gen1_manual_start_plaintext(
    mesh_device_id: int,
    duration_sec: int,
    *,
    cmd: int = GEN1_POST_HANDSHAKE_CMD,
) -> bytes:
    """Full plaintext: mesh magic + session cmd + manual-start inner record."""
    magic = mesh_prefix_bytes(mesh_device_id)
    return encode_gen1_plaintext(magic, cmd, build_gen1_manual_start_payload(duration_sec))


def gen1_stop_plaintext(mesh_device_id: int, cmd: int) -> bytes:
    """Full plaintext for stop (``0e40…`` inner record)."""
    magic = mesh_prefix_bytes(mesh_device_id)
    return encode_gen1_plaintext(magic, cmd, build_gen1_stop_payload())


def gen1_status_probe_plaintext(mesh_device_id: int, cmd: int) -> bytes:
    magic = mesh_prefix_bytes(mesh_device_id)
    return encode_gen1_plaintext(magic, cmd, build_gen1_status_probe_payload())


def decode_gen1_plaintext(plaintext: bytes) -> dict[str, Any]:
    """
    Parse gen1 cleartext after link decrypt.

    Layout: ``[magic:2][cmd:1][payload…]``
    Device responses set bit ``0x40`` on ``cmd`` (request ``0x81`` → response ``0xc1``).
    """
    if len(plaintext) < 3:
        msg = "gen1 plaintext too short (need magic + cmd)"
        raise ValueError(msg)

    magic = plaintext[0:2]
    cmd = plaintext[2]
    is_response = bool(cmd & GEN1_RESPONSE_BIT)
    base_cmd = cmd & ~GEN1_RESPONSE_BIT if is_response else cmd
    payload = plaintext[3:]

    out: dict[str, Any] = {
        "protocol": "gen1_legacy",
        "magic_hex": magic.hex(),
        "cmd": cmd,
        "cmd_hex": f"0x{cmd:02x}",
        "direction": "device" if is_response else "app",
        "payload_len": len(payload),
        "payload_hex": payload.hex(),
    }
    if is_response:
        out["request_cmd"] = base_cmd
        out["request_cmd_hex"] = f"0x{base_cmd:02x}"
    return out


def decode_gen1_inner_payload(payload: bytes) -> dict[str, Any] | None:
    """
    Best-effort decode of gen1 inner records seen in device NOTIFYs.

    Confirmed from ``gen1_pairingY-activity1`` and ``gen1_start-stop1.pcapng``:

    - **device_info** - ``01 40 00 <fw_u8> <num_stations_u16 BE> …`` (reconnect cmd ``0x04``)
    - **watering_status** - ``02 40 04 <flags_u8> <rem_quarters_u8> 40 <total_u16 LE> 00``
      - **total** ``u16 LE`` @ bytes 6-7
      - **remaining** = byte 4 x 4 seconds (``gen1_start-stop1.pcapng`` for 600 s runs)
      - byte 4 is **not** raw seconds (600 s would not fit in one byte)
    - **watering_idle** - ``02 40 01 ff ff ff ff …`` (not running)
    - **fault** - ``03 40`` + unix u32 LE + optional ``battery_mv`` u16 LE @ bytes 6-7
    - **probe_reply** - ``0c 40`` + u32 LE (status probe response)
    """
    if len(payload) < 3 or payload[1] != GEN1_INNER_RECORD_MARKER:
        return None

    sub, _ = payload[0], payload[1]

    if sub == 0x01 and len(payload) >= 6:
        return {
            "kind": "device_info",
            "model": GEN1_MODEL,
            "firmware_version": int(payload[3]),
            "num_stations": int.from_bytes(payload[4:6], "big"),
        }

    if sub == 0x02 and len(payload) >= 4 and payload[2] == 0x01:
        return {"kind": "watering_idle", "active": False}

    if (
        sub == 0x02
        and len(payload) >= 9
        and payload[2] == 0x04
        and payload[5] == GEN1_INNER_RECORD_MARKER
    ):
        total = int.from_bytes(payload[6:8], "little")
        remaining = int(payload[4]) * 4
        if total > 0:
            remaining = min(remaining, total)
        return {
            "kind": "watering_status",
            "active": True,
            "status_flags": int(payload[3]),
            "remaining_sec": remaining,
            "total_sec": total,
        }

    if sub == 0x03 and len(payload) >= 7:
        ts = struct.unpack_from("<I", payload, 2)[0]
        out: dict[str, Any] = {
            "kind": "fault",
            "active": False,
            "timestamp_unix": ts,
            "tail_hex": payload[6:].hex(),
        }
        if len(payload) >= 8:
            battery_mv = int(struct.unpack_from("<H", payload, 6)[0])
            if battery_mv > 0:
                out["battery_mv"] = battery_mv
                out["battery_percent"] = estimate_battery_percent_from_mv(battery_mv)
        return out

    if sub == 0x0C and len(payload) >= 7:
        return {
            "kind": "probe_reply",
            "value": struct.unpack_from("<I", payload, 2)[0],
        }

    if sub == 0x0D and len(payload) >= 7 and payload[2] == 0x04:
        dur = struct.unpack_from("<I", payload, 3)[0]
        return {"kind": "manual_start_ack", "duration_sec": dur}

    return None


def merge_gen1_status_record(snapshot: dict[str, dict[str, Any]], record: dict[str, Any]) -> None:
    """Merge one decoded inner record into a session snapshot (latest per kind)."""
    kind = str(record.get("kind") or "")
    if kind == "watering_status":
        snapshot["watering_status"] = record
        snapshot.pop("watering_idle", None)
    elif kind == "watering_idle":
        snapshot.pop("watering_status", None)
        snapshot["watering_idle"] = record
    elif kind == "fault":
        snapshot[kind] = record
        mv = record.get("battery_mv")
        if isinstance(mv, int) and mv > 0:
            snapshot["battery"] = {
                "kind": "battery",
                "battery_mv": mv,
                "battery_percent": record.get("battery_percent"),
            }
    elif kind:
        snapshot[kind] = record


def gen1_status_snapshot_verified(snapshot: dict[str, dict[str, Any]]) -> bool:
    """Return whether a gen1 status session produced usable device traffic."""
    if "device_info" in snapshot:
        return True
    return format_gen1_watering_status(snapshot) is not None


def format_gen1_watering_status(snapshot: dict[str, dict[str, Any]]) -> str | None:
    """Pick the best watering line from a gen1 status snapshot."""
    if "watering_status" in snapshot:
        return format_gen1_inner_status(snapshot["watering_status"])
    if "watering_idle" in snapshot:
        return format_gen1_inner_status(snapshot["watering_idle"])
    return None


def format_gen1_inner_status(status: dict[str, Any]) -> str:
    """One-line human summary for ``decode_gen1_inner_payload``."""
    kind = status.get("kind")
    if kind == "device_info":
        return (
            f"model={status.get('model', GEN1_MODEL)}  "
            f"firmware={status['firmware_version']}  "
            f"stations={status['num_stations']}"
        )
    if kind == "watering_status":
        return (
            f"watering active: remaining={status['remaining_sec']}s"
            f"  total={status['total_sec']}s"
        )
    if kind == "watering_idle":
        return "watering idle (not running)"
    if kind == "fault":
        if status.get("battery_mv"):
            pct = status.get("battery_percent")
            return f"battery={pct}% ({status['battery_mv']} mV)"
        return "status notify"
    if kind == "battery":
        return f"{status['battery_percent']}% ({status['battery_mv']} mV)"
    if kind == "probe_reply":
        return f"status probe reply: value={status.get('value')}"
    if kind == "manual_start_ack":
        return f"manual start acknowledged: {status.get('duration_sec')}s"
    return f"gen1 inner: {kind}"


def parse_gen1_station_status(
    snapshot: dict[str, dict[str, Any]] | None,
    station_id: int,
) -> dict[str, Any]:
    """
    Per-station status for HA sensors (gen1 BH1G1 / HT25 — single port).

    Same keys as :func:`orbit_codec.parse_station_status` where applicable.
    """
    out: dict[str, Any] = {
        "state": "off",
        "faults": [],
        "watering_status": None,
        "remaining_sec": None,
    }
    if snapshot is None:
        out["state"] = "unknown"
        return out
    if station_id != 0:
        return out
    ws = snapshot.get("watering_status")
    if isinstance(ws, dict) and ws.get("active"):
        out["state"] = "watering"
        out["watering_status"] = "watering"
        rem = ws.get("remaining_sec")
        if rem is not None:
            out["remaining_sec"] = int(rem)
        return out
    if snapshot.get("fault"):
        out["state"] = "fault"
        out["faults"] = ["fault"]
        return out
    if "watering_idle" in snapshot or "device_info" in snapshot:
        return out
    out["state"] = "unknown"
    return out


def parse_gen1_battery_percent_mv(
    snapshot: dict[str, dict[str, Any]] | None,
) -> tuple[int | None, int | None]:
    """Battery percent and millivolts from a gen1 status snapshot."""
    if not snapshot:
        return None, None
    bat = snapshot.get("battery")
    if isinstance(bat, dict):
        pct = bat.get("battery_percent")
        mv = bat.get("battery_mv")
        return (
            int(pct) if pct is not None else None,
            int(mv) if mv is not None else None,
        )
    fault = snapshot.get("fault")
    if isinstance(fault, dict) and fault.get("battery_mv"):
        mv = int(fault["battery_mv"])
        pct = fault.get("battery_percent")
        return (int(pct) if pct is not None else None, mv)
    return None, None


def gen1_device_info_for_registry(
    snapshot: dict[str, dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Map gen1 ``device_info`` inner record to Orbit-like ``deviceInfo`` fields."""
    if not snapshot:
        return None
    di = snapshot.get("device_info")
    if not isinstance(di, dict):
        return None
    fw = di.get("firmware_version")
    return {
        "hwVersion": str(di.get("model") or GEN1_MODEL),
        "fwVersion": str(fw) if fw is not None else None,
        "numStations": 1,
    }


def gen1_is_watering(snapshot: dict[str, Any] | None) -> bool:
    """Return whether the gen1 status snapshot shows an active manual run."""
    if not snapshot:
        return False
    ws = snapshot.get("watering_status")
    return bool(isinstance(ws, dict) and ws.get("active"))


def format_gen1_decoded(
    decoded: dict[str, Any],
    *,
    att_direction: str | None = None,
    include_hex: bool = True,
) -> str:
    """Single-line gen1 application-layer summary (after link decrypt)."""
    cmd = decoded["cmd_hex"]
    cmd_int = int(decoded["cmd"])
    tail = str(decoded["payload_hex"])
    if include_hex:
        if len(tail) > 40:
            tail = tail[:40] + "…"
        hex_part = f"  {tail}"
    else:
        hex_part = ""

    if decoded.get("request_cmd_hex"):
        role = f"response {decoded['request_cmd_hex']}→{cmd}"
    elif att_direction == "write_f":
        role = f"write {cmd}"
    elif att_direction == "notify":
        if cmd_int in GEN1_ASYNC_NOTIFY_CMDS and not (cmd_int & GEN1_RESPONSE_BIT):
            role = f"push {cmd}"
        else:
            role = f"notify {cmd}"
    elif cmd_int & GEN1_RESPONSE_BIT:
        req = decoded.get("request_cmd_hex")
        role = f"response {req}→{cmd}" if req else f"response {cmd}"
    elif cmd_int in GEN1_ASYNC_NOTIFY_CMDS:
        role = f"push {cmd}"
    else:
        role = f"cmd {cmd}"

    return f"gen1 {role}{hex_part}"
