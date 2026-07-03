"""Legacy command protocol"""

from __future__ import annotations

import struct
import time
from typing import Any

from .constants import (
    MANUAL_WATER_RUN_SEC_MAX,
    MANUAL_WATER_RUN_SEC_MIN,
    estimate_battery_percent_from_mv,
)

GEN1_RESPONSE_BIT = 0x40

# Trailing 2 bytes on 0x81 / 0x86 timestamp payloads (stable on BH1G1 units).
GEN1_DEFAULT_TIMESTAMP_TAIL = bytes([0xD4, 0xFE])

# Device async notifies (client ACK = cmd | 0x40). Onboard/reconnect often 0xa4-0xac;
# during manual watering also 0x87-0x8c (status/fault); faults 0x88.
GEN1_ASYNC_NOTIFY_CMDS = (
    frozenset(range(0xA4, 0xAD))
    | frozenset(range(0xB1, 0xC0))
    | frozenset(range(0x87, 0x8D))
)

# First application cmd after reconnect/onboard handshake (0x81-0x86).
GEN1_POST_HANDSHAKE_CMD = 0x87

# After commit (0x86): wait for async NOTIFYs to go quiet.
GEN1_COMMIT_DRAIN_MAX_S = 2.0
GEN1_COMMIT_QUIET_S = 0.35

# Inner record marker (also GEN1_RESPONSE_BIT on the outer cmd byte).
GEN1_INNER_RECORD_MARKER = 0x40

# Mesh register inner record (0x84 / 0x85): 00 40 01 <mesh LE16> 00 00 00 00.
GEN1_MESH_REGISTER_PREFIX = bytes([0x00, 0x40, 0x01])
GEN1_MESH_REGISTER_CMDS = frozenset({0x84, 0x85})

# Stop payload LE u16 reason (1000) and trailing code byte from gen1_pairingY-activity1.
GEN1_DEFAULT_STOP_REASON = 1000
GEN1_DEFAULT_STOP_CODE = 0x02

# Only gen1 hose-timer SKU (1 port); not on the BLE device_info wire.
GEN1_MODEL = "HT25"


def device_id_from_plaintext(plaintext: bytes) -> int | None:
    """LE16 session magic from the first 2 bytes of a gen1 plaintext."""
    if len(plaintext) < 2:
        return None
    return int.from_bytes(plaintext[0:2], "little")


def encode_gen1_plaintext(magic: bytes, cmd: int, payload: bytes = b"") -> bytes:
    """Build [magic:2][cmd:1][payload...]."""
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
    """Payload for gen1 cmds 0x81 and 0x86: 05 40 + uint32 LE unix + 2-byte tail."""
    if len(tail) != 2:
        msg = "tail must be 2 bytes"
        raise ValueError(msg)
    ts = int(time.time() if now is None else now)
    return bytes([0x05, 0x40]) + struct.pack("<I", ts) + tail


def gen1_link_plaintext_acceptable(plaintext: bytes, *, magic: bytes | None = None) -> bool:
    if len(plaintext) < 3:
        return False
    if plaintext[2] == 0x00:
        return False
    if magic is not None and plaintext[0:2] != magic:
        return False
    return True


def gen1_onboard_write_plaintexts(
    device_id: int,
    *,
    tail: bytes = GEN1_DEFAULT_TIMESTAMP_TAIL,
    now: int | None = None,
) -> list[tuple[str, bytes]]:
    magic = struct.pack("<H", int(device_id))
    if now is None:
        ts_ping = build_gen1_timestamp_payload(tail=tail)
        ts_commit = build_gen1_timestamp_payload(tail=tail)
    else:
        ts_ping = build_gen1_timestamp_payload(tail=tail, now=now)
        ts_commit = build_gen1_timestamp_payload(tail=tail, now=now + 3)
    reg = GEN1_MESH_REGISTER_PREFIX + magic + bytes(4)
    return [
        ("gen1 onboard: ping 0x81", encode_gen1_plaintext(magic, 0x81, ts_ping)),
        ("gen1 onboard: 0x02", encode_gen1_plaintext(magic, 0x02, bytes.fromhex("0140000000"))),
        ("gen1 onboard: 0x03", encode_gen1_plaintext(magic, 0x03, bytes.fromhex("034000000000000000"))),
        ("gen1 onboard: register mesh 0x84", encode_gen1_plaintext(magic, 0x84, reg)),
        ("gen1 onboard: 0x05", encode_gen1_plaintext(magic, 0x05, bytes.fromhex("0140000000"))),
        ("gen1 onboard: commit 0x86", encode_gen1_plaintext(magic, 0x86, ts_commit)),
    ]


def gen1_mesh_attach_plaintexts(
    device_id: int,
    *,
    label_prefix: str,
    include_85_clear: bool,
    tail: bytes = GEN1_DEFAULT_TIMESTAMP_TAIL,
    now: int | None = None,
) -> list[tuple[str, bytes]]:
    magic = struct.pack("<H", int(device_id))
    if now is None:
        ts_ping = build_gen1_timestamp_payload(tail=tail)
        ts_commit = build_gen1_timestamp_payload(tail=tail)
    else:
        ts_ping = build_gen1_timestamp_payload(tail=tail, now=now)
        ts_commit = build_gen1_timestamp_payload(tail=tail, now=now + 3)
    reg = GEN1_MESH_REGISTER_PREFIX + magic + bytes(4)
    steps: list[tuple[str, bytes]] = [
        (f"{label_prefix}: ping 0x81", encode_gen1_plaintext(magic, 0x81, ts_ping)),
        (f"{label_prefix}: poll 0x02", encode_gen1_plaintext(magic, 0x02, bytes.fromhex("024000"))),
        (f"{label_prefix}: 0x03", encode_gen1_plaintext(magic, 0x03, bytes.fromhex("034000000000000000"))),
        (
            f"{label_prefix}: device_info 0x04",
            encode_gen1_plaintext(magic, 0x04, bytes.fromhex("0140000000")),
        ),
        (f"{label_prefix}: 0x85 register", encode_gen1_plaintext(magic, 0x85, reg)),
    ]
    if include_85_clear:
        steps.append(
            (
                f"{label_prefix}: 0x85 clear",
                encode_gen1_plaintext(magic, 0x85, bytes.fromhex("004000000000000000")),
            )
        )
    steps.extend(
        [
            (f"{label_prefix}: 0x85 tail", encode_gen1_plaintext(magic, 0x85, bytes.fromhex("094000"))),
            (f"{label_prefix}: commit 0x86", encode_gen1_plaintext(magic, 0x86, ts_commit)),
        ]
    )
    return steps


def build_gen1_manual_start_payload(duration_sec: int) -> bytes:
    """
    Inner record for manual run start: 0d 40 04 + uint32 LE seconds + 00 00 pad.

    From observed wire format (60 s -> 0d40043c0000000000).
    """
    sec = int(duration_sec)
    if not MANUAL_WATER_RUN_SEC_MIN <= sec <= MANUAL_WATER_RUN_SEC_MAX:
        msg = (
            f"duration_sec must be in [{MANUAL_WATER_RUN_SEC_MIN}, {MANUAL_WATER_RUN_SEC_MAX}], got {sec}"
        )
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
    """Inner record for user stop: 0e 40 e8 03 02 00 when reason=1000, code=0x02."""
    r = int(reason)
    if not 0 <= r <= 0xFFFF:
        msg = "reason must be 0..65535"
        raise ValueError(msg)
    c = int(code) & 0xFF
    return bytes([0x0E, GEN1_INNER_RECORD_MARKER]) + struct.pack("<HBB", r, c, 0)


def decode_gen1_plaintext(plaintext: bytes) -> dict[str, Any]:
    """
    Layout: [magic:2][cmd:1][payload...]
    Device responses set bit 0x40 on cmd (request 0x81 -> response 0xc1).
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


def assigned_device_id_from_register_response(decoded: dict[str, Any]) -> int | None:
    req = decoded.get("request_cmd")
    if req not in GEN1_MESH_REGISTER_CMDS:
        return None
    try:
        payload = bytes.fromhex(str(decoded.get("payload_hex") or ""))
    except ValueError:
        return None
    if len(payload) < 5 or payload[0:3] != GEN1_MESH_REGISTER_PREFIX:
        return None
    device_id = int.from_bytes(payload[3:5], "little")
    return device_id or None


def decode_gen1_inner_payload(
    payload: bytes,
    *,
    direction: str = "notify",
) -> dict[str, Any] | None:
    """
    Best-effort decode of gen1 inner records seen in device NOTIFYs (and host writes).

    direction ("notify" | "write_f") is accepted for trace symmetry; the
    record layout is the same in both directions.

    Known record shapes:

    - device_info     - 01 40 00 <fw_u8> <num_stations_u16 BE> ... (reconnect cmd 0x04)
    - watering_status - 02 40 04 <flags_u8> <rem_quarters_u8> 40 <total_u16 LE> 00
      - total u16 LE @ bytes 6-7
      - remaining = byte 4 x 4 seconds (e.g. 600 s total with byte 4 = 150)
    - watering_idle   - 02 40 01 ff ff ff ff ... (not running)
    - fault           - 03 40 + unix u32 LE + optional battery_mv u16 LE @ bytes 6-7
    - probe_reply     - 0c 40 + u32 LE (status probe response)
    """
    del direction  # accepted for trace symmetry; layout is direction-independent
    if len(payload) < 3 or payload[1] != GEN1_INNER_RECORD_MARKER:
        return None

    sub = payload[0]

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
    if "device_info" in snapshot:
        return True
    return "watering_status" in snapshot or "watering_idle" in snapshot


def parse_gen1_station_status(
    snapshot: dict[str, dict[str, Any]] | None,
    station_id: int,
) -> dict[str, Any]:
    """
    Per-station status for HA sensors
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


__all__ = [
    "GEN1_ASYNC_NOTIFY_CMDS",
    "GEN1_COMMIT_DRAIN_MAX_S",
    "GEN1_COMMIT_QUIET_S",
    "GEN1_DEFAULT_STOP_CODE",
    "GEN1_DEFAULT_STOP_REASON",
    "GEN1_DEFAULT_TIMESTAMP_TAIL",
    "GEN1_INNER_RECORD_MARKER",
    "GEN1_MODEL",
    "GEN1_POST_HANDSHAKE_CMD",
    "GEN1_RESPONSE_BIT",
    "assigned_device_id_from_register_response",
    "build_gen1_manual_start_payload",
    "build_gen1_stop_payload",
    "decode_gen1_inner_payload",
    "decode_gen1_plaintext",
    "encode_gen1_plaintext",
    "gen1_device_info_for_registry",
    "gen1_link_plaintext_acceptable",
    "gen1_mesh_attach_plaintexts",
    "gen1_onboard_write_plaintexts",
    "gen1_status_snapshot_verified",
    "merge_gen1_status_record",
    "device_id_from_plaintext",
    "parse_gen1_battery_percent_mv",
    "parse_gen1_station_status",
]
