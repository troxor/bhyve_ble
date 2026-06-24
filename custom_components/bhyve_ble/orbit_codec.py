from __future__ import annotations

import base64
import struct
from typing import Any

from google.protobuf.json_format import MessageToDict

from .const import MAX_TIMER_PORTS, VALID_TIMER_PORT_COUNTS

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
    if isinstance(obj, str) and (obj.startswith(("OrbitPbApi_", "BhyveAgApi_"))):
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
        msg = "plaintext too short"
        raise ValueError(msg)
    magic = struct.unpack_from("<I", plaintext, 0)[0]
    if magic != MAGIC_LE:
        msg = f"bad magic 0x{magic:08x}"
        raise ValueError(msg)
    inner_len = struct.unpack_from("<H", plaintext, 4)[0]
    body = plaintext[6:-2]
    crc_wire = struct.unpack_from("<H", plaintext, len(plaintext) - 2)[0]
    crc_calc = crc16_ccitt_init0(plaintext[:-2])
    if crc_calc != crc_wire:
        msg = f"CRC mismatch: wire=0x{crc_wire:04x} calc=0x{crc_calc:04x}"
        raise ValueError(msg)
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
    mode_map = {
        "off": 0,
        "offMode": 0,
        "auto": 1,
        "autoMode": 1,
        "manual": 2,
        "manualMode": 2,
    }
    if m not in mode_map:
        msg = f"mode must be one of {sorted(mode_map)}, got {mode!r}"
        raise ValueError(msg)
    mode_num = mode_map[m]

    tm = _write_varint((1 << 3) | 0) + _write_varint(mode_num)
    if mode_num == 2:
        if run_time_sec is None:
            msg = "run_time_sec is required for manualMode"
            raise ValueError(msg)
        n = int(run_time_sec)
        if n < MANUAL_WATER_RUN_SEC_MIN or n > MANUAL_WATER_RUN_SEC_MAX:
            msg = f"run_time_sec must be in [{MANUAL_WATER_RUN_SEC_MIN}, {MANUAL_WATER_RUN_SEC_MAX}], got {n}"
            raise ValueError(msg)
        st = (
            _write_varint((1 << 3) | 0)
            + _write_varint(int(station_id))
            + _write_varint((2 << 3) | 0)
            + _write_varint(n)
        )
        mmp = _write_varint((3 << 3) | 2) + _write_varint(len(st)) + st
        tm += _write_varint((2 << 3) | 2) + _write_varint(len(mmp)) + mmp
    else:
        # offMode / autoMode: empty manualModeParams (matches app captures).
        tm += _write_varint((2 << 3) | 2) + _write_varint(0)
    msg_body = _write_varint((14 << 3) | 2) + _write_varint(len(tm)) + tm
    return wrap_orbit_ble_body(msg_body)


def encode_manual_watering_plaintext(
    run_time_sec: int,
    *,
    station_id: int = 0,
) -> bytes:
    """Manual ``timerMode`` for one station (lab ``start`` uses ``station_id=0``)."""
    return encode_timer_mode_plaintext(
        "manualMode",
        run_time_sec=run_time_sec,
        station_id=station_id,
    )


def encode_get_device_status_info_plaintext() -> bytes:
    msg_body = _write_varint((15 << 3) | 2) + _write_varint(0)
    return wrap_orbit_ble_body(msg_body)


def encode_get_battery_status_plaintext() -> bytes:
    """Encode ``getBatteryStatus`` request (empty submessage). Observed body: ``ea 02 00``."""
    msg_body = _write_varint((45 << 3) | 2) + _write_varint(0)
    return wrap_orbit_ble_body(msg_body)


def normalize_orbit_message_for_status(msg: dict) -> dict:
    """
    Fold top-level Orbit oneof branches into ``deviceStatusInfo``.

    Gen2 NOTIFYs often return ``wateringStatus`` or ``batteryStatus`` as sibling oneofs,
    not nested under ``deviceStatusInfo``. HA sensors read the nested form.
    """
    out = dict(msg)
    dsi = dict(out.get("deviceStatusInfo") or {})

    ws = out.get("wateringStatus")
    if isinstance(ws, dict) and ws:
        prev_ws = dsi.get("wateringStatus")
        if isinstance(prev_ws, dict):
            dsi["wateringStatus"] = deep_merge_partial_proto_dict(prev_ws, ws)
        else:
            dsi["wateringStatus"] = ws

    bat = out.get("batteryStatus")
    if isinstance(bat, dict) and bat:
        prev_bat = dsi.get("batteryStatus")
        if isinstance(prev_bat, dict):
            dsi["batteryStatus"] = deep_merge_partial_proto_dict(prev_bat, bat)
        else:
            dsi["batteryStatus"] = bat

    fs = out.get("faultStatus")
    if isinstance(fs, dict) and fs:
        prev_fs = dsi.get("faultStatus")
        if isinstance(prev_fs, dict):
            dsi["faultStatus"] = deep_merge_partial_proto_dict(prev_fs, fs)
        else:
            dsi["faultStatus"] = fs

    if dsi:
        out["deviceStatusInfo"] = dsi
    return out


def deep_merge_partial_proto_dict(base: dict, update: dict) -> dict:
    """
    Recursively merge ``update`` into ``base``.

    Consecutive BLE notifications often send the same oneof branch (e.g.
    ``deviceStatusInfo``) with different subsets of fields. A shallow merge at the
    branch level would drop siblings such as ``batteryStatus`` when a later payload
    omits them even though the device did not clear those values.
    """
    out = dict(base)
    for key, val in update.items():
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = deep_merge_partial_proto_dict(out[key], val)
        else:
            out[key] = val
    return out


def deep_merge_device_status_info(base: dict, update: dict) -> dict:
    """
    Merge ``deviceStatusInfo`` NOTIFY fragments.

  When a newer payload includes an empty ``wateringStatusSummary`` without legacy
    ``wateringStatus``, drop stale legacy only if ``deviceStatus`` indicates idle.
    Gen2 often sends ``sessions: []`` during active watering while legacy
    ``wateringStatus`` / ``deviceStatus: wateringInProgress`` carry the real state.
    """
    merged = deep_merge_partial_proto_dict(base, update)
    if "wateringStatusSummary" not in update or "wateringStatus" in update:
        return merged
    sessions = (update.get("wateringStatusSummary") or {}).get("sessions") or []
    if sessions:
        return merged
    ds = short_orbit_enum_name(update.get("deviceStatus"))
    if not ds:
        ds = short_orbit_enum_name(merged.get("deviceStatus"))
    if ds in ("deviceIdle", "deviceOff"):
        merged.pop("wateringStatus", None)
    return merged


# Hose-timer battery mV to percent (linear, 2400-3000 mV).
BATTERY_MV_EMPTY = 2400
BATTERY_MV_FULL = 3000


def mv_to_percent_linear(mv: int, mv_empty: int, mv_full: int) -> int:
    """Clamp mV to [empty, full], linear scale to 0-100, truncate toward zero."""
    low = min(mv_empty, mv_full)
    high = max(mv_empty, mv_full)
    if high <= low:
        return 0
    clamped = max(low, min(mv, high))
    return int((clamped - low) * 100 / (high - low))


def estimate_battery_percent_from_mv(mv: int) -> int:
    """Map pack millivolts to 0-100 when the device omits ``batteryLevelPercent``."""
    return mv_to_percent_linear(mv, BATTERY_MV_EMPTY, BATTERY_MV_FULL)


def parse_battery_percent_mv_from_decoded(decoded: dict | None) -> tuple[int | None, int | None]:
    """Read battery percent and voltage from ``deviceStatusInfo`` (or top-level ``batteryStatus``)."""
    if not decoded:
        return None, None
    m = normalize_orbit_message_for_status(decoded.get("message") or {})
    dsi = m.get("deviceStatusInfo") or {}
    bat = dsi.get("batteryStatus") or m.get("batteryStatus") or {}

    pct_raw = bat.get("batteryLevelPercent")
    if pct_raw is None:
        pct_raw = dsi.get("batteryLevelPercent")

    mv_raw = bat.get("batteryLevelMV")
    if mv_raw is None:
        mv_raw = dsi.get("batteryLevelMV")

    pct: int | None = None
    if pct_raw is not None:
        try:
            p = int(pct_raw)
            pct = max(0, min(100, p))
        except TypeError, ValueError:
            pct = None

    mv: int | None = None
    if mv_raw is not None:
        try:
            mv = int(mv_raw)
        except TypeError, ValueError:
            mv = None

    return pct, mv


def resolve_battery_percent_display(
    pct: int | None, mv: int | None
) -> tuple[int | None, str | None]:
    """Percent for the Battery entity: device value, else estimate from mV."""
    if pct is not None:
        return pct, "device"
    if mv is not None:
        return estimate_battery_percent_from_mv(mv), "estimated_mv"
    return None, None


def normalize_num_stations(raw: int | None) -> int:
    """Clamp ``deviceInfo.numStations`` to known hose-timer port counts (1, 2, or 4)."""
    if raw is None:
        return 1
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 1
    if n in VALID_TIMER_PORT_COUNTS:
        return n
    if n < 1:
        return 1
    if n > MAX_TIMER_PORTS:
        return MAX_TIMER_PORTS
    return 2


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
        return normalize_num_stations(int(n))
    except TypeError, ValueError:
        return None


def short_orbit_enum_name(value: Any) -> str:
    """``OrbitPbApi_WateringStatus_Status_wateringInProgress`` → ``wateringInProgress``."""
    if value is None:
        return ""
    s = str(value)
    if "_" in s:
        return s.rsplit("_", 1)[-1]
    return s


_STATION_FAULT_TYPE_KEYS: tuple[str, ...] = (
    "unavailable",
    "shortCircuit",
    "overcurrent",
    "undercurrent",
    "noFlow",
    "highFlow",
    "lowFlow",
)

_ACTIVE_WATERING_STATUSES: frozenset[str] = frozenset(
    {
        "wateringInProgress",
        "programPreDelay",
        "programPostDelay",
        "pumpDelay",
        "stationDelay",
    }
)

_ACTIVE_SWITCH_STATUSES: frozenset[str] = frozenset(
    {
        "wateringInProgress",
        "programPreDelay",
        "programPostDelay",
    }
)


def _station_fault_flag_set(fault_status: dict[str, Any], station_id: int) -> bool:
    if station_id < 32:
        flags = fault_status.get("stationFaultFlags_0_31")
    else:
        flags = fault_status.get("stationFaultFlags_32_63")
        station_id -= 32
    if flags is None:
        return False
    try:
        return bool(int(flags) & (1 << station_id))
    except TypeError, ValueError:
        return False


def parse_station_faults(decoded: dict | None, station_id: int) -> list[str]:
    """Fault type names for one station from ``deviceStatusInfo.faultStatus``."""
    if not decoded:
        return []
    m = normalize_orbit_message_for_status(decoded.get("message") or {})
    dsi = m.get("deviceStatusInfo") or {}
    fault_status = dsi.get("faultStatus") or {}
    faults: list[str] = []

    for entry in fault_status.get("stationFaults") or []:
        if not isinstance(entry, dict):
            continue
        try:
            sid = int(entry.get("stationId", -1))
        except TypeError, ValueError:
            continue
        if sid != station_id:
            continue
        for key in _STATION_FAULT_TYPE_KEYS:
            if entry.get(key) is not None:
                faults.append(key)

    if _station_fault_flag_set(fault_status, station_id) and "station_fault" not in faults:
        faults.append("station_fault")

    return faults


def _watering_session_for_station(sessions: list[Any], station_id: int) -> dict[str, Any] | None:
    for sess in sessions:
        if not isinstance(sess, dict):
            continue
        try:
            cur = int(sess.get("currentStationId", -1))
        except TypeError, ValueError:
            continue
        if cur == station_id:
            return sess
    return None


def _legacy_watering_applies_to_station(
    ws: dict[str, Any], station_id: int, *, num_stations: int
) -> bool:
    st_name = short_orbit_enum_name(ws.get("status"))
    if st_name not in _ACTIVE_WATERING_STATUSES:
        return False
    cur = ws.get("currentStationId")
    if cur is not None:
        try:
            return int(cur) == station_id
        except TypeError, ValueError:
            return False
    return num_stations == 1 and station_id == 0


def _timer_mode_run_time_sec(dsi: dict[str, Any], station_id: int) -> int | None:
    tm = dsi.get("timerMode") or {}
    if short_orbit_enum_name(tm.get("mode")) != "manualMode":
        return None
    mmp = tm.get("manualModeParams") or {}
    for st in mmp.get("stationInfo") or []:
        if not isinstance(st, dict):
            continue
        try:
            sid = int(st.get("stationId", -1))
        except TypeError, ValueError:
            continue
        if sid != station_id:
            continue
        raw = st.get("runTimeSec")
        if raw is None:
            return None
        try:
            return int(raw)
        except TypeError, ValueError:
            return None
    return None


def _manual_mode_targets_station(dsi: dict[str, Any], station_id: int) -> bool:
    """True when ``timerMode.manualMode`` lists ``station_id`` (or single-port default)."""
    tm = dsi.get("timerMode") or {}
    if short_orbit_enum_name(tm.get("mode")) != "manualMode":
        return False
    mmp = tm.get("manualModeParams") or {}
    stations = mmp.get("stationInfo") or []
    if not stations:
        return station_id == 0
    for st in stations:
        if not isinstance(st, dict):
            continue
        try:
            if int(st.get("stationId", -1)) == station_id:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _session_from_device_status_watering(
    dsi: dict[str, Any], station_id: int, *, num_stations: int
) -> dict[str, Any] | None:
    """Fallback when ``deviceStatus`` is ``wateringInProgress`` (lab status shape)."""
    if short_orbit_enum_name(dsi.get("deviceStatus")) != "wateringInProgress":
        return None
    ws = dsi.get("wateringStatus") or {}
    if isinstance(ws, dict) and ws:
        if _legacy_watering_applies_to_station(ws, station_id, num_stations=num_stations):
            return {
                "status": ws.get("status") or "wateringInProgress",
                "currentTimeRemainingSec": ws.get("currentTimeRemainingSec"),
            }
    if not _manual_mode_targets_station(dsi, station_id) and not (
        num_stations == 1 and station_id == 0
    ):
        return None
    rem: int | None = None
    if isinstance(ws, dict):
        raw = ws.get("currentTimeRemainingSec")
        if raw is not None:
            try:
                rem = int(raw)
            except (TypeError, ValueError):
                rem = None
    if rem is None:
        rem = _timer_mode_run_time_sec(dsi, station_id)
    return {
        "status": "wateringInProgress",
        "currentTimeRemainingSec": rem,
    }


def _resolve_watering_session(
    decoded: dict,
    station_id: int,
    *,
    num_stations: int = 1,
) -> dict[str, Any] | None:
    """Prefer ``wateringStatusSummary.sessions``, then nested or top-level ``wateringStatus``."""
    m = normalize_orbit_message_for_status(decoded.get("message") or {})
    dsi = m.get("deviceStatusInfo") or {}
    has_summary = "wateringStatusSummary" in dsi
    sessions = (dsi.get("wateringStatusSummary") or {}).get("sessions") or []

    sess = _watering_session_for_station(sessions, station_id)
    if sess:
        return sess

    for ws in (dsi.get("wateringStatus"), m.get("wateringStatus")):
        if isinstance(ws, dict) and _legacy_watering_applies_to_station(
            ws, station_id, num_stations=num_stations
        ):
            return {
                "status": ws.get("status"),
                "currentTimeRemainingSec": ws.get("currentTimeRemainingSec"),
            }

    sess = _session_from_device_status_watering(
        dsi, station_id, num_stations=num_stations
    )
    if sess:
        return sess

    if has_summary:
        return None
    return None


def parse_station_status(
    decoded: dict | None,
    station_id: int,
    *,
    num_stations: int = 1,
) -> dict[str, Any]:
    """
    Per-station status for HA sensors.

    Returns ``state`` (``off`` | ``watering`` | ``delay`` | ``fault`` | ``unknown``),
    optional ``watering_status`` / ``remaining_sec``, and ``faults`` list.
    Defaults to ``off`` when no watering activity is reported.
    """
    faults = parse_station_faults(decoded, station_id)
    out: dict[str, Any] = {
        "state": "off",
        "faults": faults,
        "watering_status": None,
        "remaining_sec": None,
    }
    if decoded is None:
        out["state"] = "unknown"
        return out

    sess = _resolve_watering_session(
        decoded, station_id, num_stations=num_stations
    )

    if sess:
        st_name = short_orbit_enum_name(sess.get("status"))
        out["watering_status"] = st_name or None
        rem = sess.get("currentTimeRemainingSec")
        if rem is not None:
            try:
                out["remaining_sec"] = int(rem)
            except TypeError, ValueError:
                out["remaining_sec"] = None
        if st_name in _ACTIVE_WATERING_STATUSES:
            out["state"] = "watering" if st_name == "wateringInProgress" else "delay"
        elif st_name in ("wateringComplete", "stationComplete") or st_name:
            out["state"] = "off"

    if faults and out["state"] == "off":
        out["state"] = "fault"

    return out


def station_is_actively_watering(
    decoded: dict | None, station_id: int, *, num_stations: int = 1
) -> bool | None:
    """
    Whether ``station_id`` is currently in an active watering state (manual/schedule).

    Uses ``wateringStatusSummary.sessions`` when present; otherwise legacy ``wateringStatus``
    plus ``currentStationId``. Returns ``None`` if status is unknown.
    """
    if not decoded:
        return None

    m = normalize_orbit_message_for_status(decoded.get("message") or {})
    dsi = m.get("deviceStatusInfo") or {}
    has_summary = "wateringStatusSummary" in dsi

    sess = _resolve_watering_session(
        decoded, station_id, num_stations=num_stations
    )
    if sess:
        st_name = short_orbit_enum_name(sess.get("status"))
        if not st_name:
            return None
        return st_name in _ACTIVE_SWITCH_STATUSES

    ds_name = short_orbit_enum_name(dsi.get("deviceStatus"))
    if ds_name == "wateringInProgress":
        fallback = _session_from_device_status_watering(
            dsi, station_id, num_stations=num_stations
        )
        if fallback:
            return True

    if has_summary and ds_name in ("deviceIdle", "deviceOff", ""):
        return False
    if not dsi and not m.get("wateringStatus"):
        return None
    return False


def encode_get_device_info_plaintext() -> bytes:
    msg_body = _write_varint((22 << 3) | 2) + _write_varint(0)
    return wrap_orbit_ble_body(msg_body)
