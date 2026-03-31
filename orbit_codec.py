from __future__ import annotations

import base64
import struct
from typing import Any

from google.protobuf.json_format import MessageToDict

# Gencode version must stay <= Home Assistant's bundled google.protobuf (often 6.32.x).
from .orbit_pb_api_pb2 import OrbitPbApi_Message

MAGIC_LE = 0x0F5A77AA

# Manual watering run duration bounds (seconds), matching the vendor app range.
MANUAL_WATER_RUN_SEC_MIN = 15
MANUAL_WATER_RUN_SEC_MAX = 4 * 3600


def crc16_ccitt_init0(data: bytes) -> int:
    crc = 0
    poly = 0x1021
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc


def _write_varint(n: int) -> bytes:
    n = int(n) & 0xFFFFFFFFFFFFFFFF
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _pb_snake_to_camel_field(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _normalize_enum_strings(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _normalize_enum_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_enum_strings(v) for v in obj]
    if isinstance(obj, str) and (obj.startswith("OrbitPbApi_") or obj.startswith("BhyveAgApi_")):
        if "_" in obj:
            return obj.rsplit("_", 1)[-1]
        return obj
    return obj


def wrap_orbit_ble_body(protobuf_body: bytes) -> bytes:
    magic = struct.pack("<I", MAGIC_LE)
    inner = len(protobuf_body) + 2
    head = magic + struct.pack("<H", inner) + protobuf_body
    crc = crc16_ccitt_init0(head)
    return head + struct.pack("<H", crc)


def unwrap_orbit_ble_plaintext(plaintext: bytes) -> tuple[bytes, dict[str, Any]]:
    if len(plaintext) < 8:
        raise ValueError("plaintext too short")
    magic = struct.unpack_from("<I", plaintext, 0)[0]
    if magic != MAGIC_LE:
        raise ValueError(f"bad magic 0x{magic:08x}")
    inner_len = struct.unpack_from("<H", plaintext, 4)[0]
    body = plaintext[6:-2]
    crc_wire = struct.unpack_from("<H", plaintext, len(plaintext) - 2)[0]
    crc_calc = crc16_ccitt_init0(plaintext[:-2])
    if crc_calc != crc_wire:
        raise ValueError(f"CRC mismatch: wire=0x{crc_wire:04x} calc=0x{crc_calc:04x}")
    meta = {
        "totalBytes": len(plaintext),
        "innerLengthField": inner_len,
        "protobufBytes": len(body),
        "wireChecksumUInt16LE": crc_wire,
    }
    return body, meta


def _message_to_jsonable(msg: dict[str, Any]) -> dict[str, Any]:
    def conv(x: Any) -> Any:
        if isinstance(x, dict):
            return {k: conv(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [conv(v) for v in x]
        if isinstance(x, bytes):
            return base64.b64encode(x).decode("ascii")
        return x

    return conv(msg)


def decode_orbit_ble_plaintext(plaintext: bytes) -> dict[str, Any]:
    body, meta = unwrap_orbit_ble_plaintext(plaintext)
    parsed = OrbitPbApi_Message()
    parsed.ParseFromString(body)
    branch = parsed.WhichOneof("message")
    if branch:
        meta["oneof"] = _pb_snake_to_camel_field(branch)
    raw = MessageToDict(
        parsed,
        preserving_proto_field_name=True,
        use_integers_for_enums=False,
    )
    msgj = _normalize_enum_strings(raw)
    msgj = _message_to_jsonable(msgj)
    return {"_framing": meta, "message": msgj}


def encode_timer_mode_plaintext(
    mode: str,
    *,
    run_time_sec: int | None = None,
    station_id: int = 0,
) -> bytes:
    m = str(mode)
    mode_map = {"off": 0, "offMode": 0, "auto": 1, "autoMode": 1, "manual": 2, "manualMode": 2}
    if m not in mode_map:
        raise ValueError(f"mode must be one of {sorted(mode_map)}, got {mode!r}")
    mode_num = mode_map[m]

    tm = _write_varint((1 << 3) | 0) + _write_varint(mode_num)
    if mode_num == 2:
        if run_time_sec is None:
            raise ValueError("run_time_sec is required for manualMode")
        n = int(run_time_sec)
        if n < MANUAL_WATER_RUN_SEC_MIN or n > MANUAL_WATER_RUN_SEC_MAX:
            raise ValueError(
                f"run_time_sec must be in [{MANUAL_WATER_RUN_SEC_MIN}, {MANUAL_WATER_RUN_SEC_MAX}], got {n}"
            )
        st = (
            _write_varint((1 << 3) | 0)
            + _write_varint(int(station_id))
            + _write_varint((2 << 3) | 0)
            + _write_varint(n)
        )
        mmp = _write_varint((3 << 3) | 2) + _write_varint(len(st)) + st
        tm += _write_varint((2 << 3) | 2) + _write_varint(len(mmp)) + mmp
    msg_body = _write_varint((14 << 3) | 2) + _write_varint(len(tm)) + tm
    return wrap_orbit_ble_body(msg_body)


def encode_get_device_status_info_plaintext() -> bytes:
    msg_body = _write_varint((15 << 3) | 2) + _write_varint(0)
    return wrap_orbit_ble_body(msg_body)


def parse_num_stations_from_decoded(decoded: dict | None) -> int | None:
    """Read ``deviceInfo.numStations`` from a decoded Orbit BLE message (if present)."""
    if not decoded:
        return None
    m = decoded.get("message") or {}
    di = m.get("deviceInfo") or {}
    n = di.get("numStations")
    if n is None:
        return None
    try:
        return int(n)
    except (TypeError, ValueError):
        return None


def station_is_actively_watering(
    decoded: dict | None, station_id: int, *, num_stations: int = 1
) -> bool | None:
    """Whether ``station_id`` is currently in an active watering state (manual/schedule).

    Uses ``wateringStatusSummary.sessions`` when present; otherwise legacy ``wateringStatus``
    plus ``currentStationId``. Returns ``None`` if status is unknown.
    """
    if not decoded:
        return None
    m = decoded.get("message") or {}
    dsi = m.get("deviceStatusInfo") or {}

    wss = dsi.get("wateringStatusSummary") or {}
    sessions = wss.get("sessions") or []
    if sessions:
        for sess in sessions:
            try:
                cur = int(sess.get("currentStationId", -1))
            except (TypeError, ValueError):
                continue
            if cur != station_id:
                continue
            st = sess.get("status")
            if st is None:
                return None
            return str(st) in ("wateringInProgress", "programPreDelay", "programPostDelay")
        return False

    ws = dsi.get("wateringStatus") or {}
    st = ws.get("status")
    if st is None:
        return None
    active = str(st) in ("wateringInProgress", "programPreDelay", "programPostDelay")
    cur = ws.get("currentStationId")
    if cur is not None:
        try:
            if int(cur) != station_id:
                return False
        except (TypeError, ValueError):
            return None
        return active
    if num_stations > 1:
        return None
    return active


def encode_get_device_info_plaintext() -> bytes:
    msg_body = _write_varint((22 << 3) | 2) + _write_varint(0)
    return wrap_orbit_ble_body(msg_body)
