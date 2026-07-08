from __future__ import annotations

import base64
import struct
from typing import Any

from google.protobuf.json_format import MessageToDict

from .constants import (
    MANUAL_WATER_RUN_SEC_MAX,
    MANUAL_WATER_RUN_SEC_MIN,
    MAX_TIMER_PORTS,
    VALID_TIMER_PORT_COUNTS,
    estimate_battery_percent_from_mv,
)

# Gencode from hose_timer_ble.proto (capture-derived Gen 2 subset).
# Regenerate: see hose_timer_ble.proto header. Pin gencode to protobuf 6.32.x.
from .hose_timer_ble_pb2 import BleEnvelope

MAGIC_LE = 0x0F5A77AA
MAGIC_BYTES = MAGIC_LE.to_bytes(4, "little")


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


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, bytes):
        return base64.b64encode(obj).decode("ascii")
    return obj


def wrap_gen2_ble_body(protobuf_body: bytes) -> bytes:
    inner = len(protobuf_body) + 2
    head = MAGIC_BYTES + struct.pack("<H", inner) + protobuf_body
    crc = crc16_ccitt_init0(head)
    return head + struct.pack("<H", crc)


def decode_gen2_ble_plaintext(plaintext: bytes) -> dict[str, Any]:
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
    meta: dict[str, Any] = {
        "totalBytes": len(plaintext),
        "innerLengthField": inner_len,
        "protobufBytes": len(body),
        "wireChecksumUInt16LE": crc_wire,
    }

    parsed = BleEnvelope()
    parsed.ParseFromString(body)
    branch = parsed.WhichOneof("payload")
    if branch:
        parts = branch.split("_")
        meta["oneof"] = parts[0] + "".join(p.capitalize() for p in parts[1:])
    raw = MessageToDict(
        parsed,
        preserving_proto_field_name=True,
        use_integers_for_enums=False,
    )
    return {"_framing": meta, "message": _to_jsonable(raw)}


def encode_timer_mode_plaintext(
    mode: str,
    *,
    run_time_sec: int | None = None,
    station_id: int = 0,
) -> bytes:
    """Encode manual start (manualMode) or stop (offMode) timerMode oneof."""
    m = str(mode)
    if m in ("off", "offMode"):
        mode_num = 0
    elif m in ("manual", "manualMode"):
        mode_num = 2
    else:
        msg = "mode must be offMode or manualMode"
        raise ValueError(msg)

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
        tm += _write_varint((2 << 3) | 2) + _write_varint(0)
    msg_body = _write_varint((14 << 3) | 2) + _write_varint(len(tm)) + tm
    return wrap_gen2_ble_body(msg_body)


def _encode_ble_envelope(**oneof_branch: object) -> bytes:
    """Serialize BleEnvelope with exactly one payload branch set."""
    msg = BleEnvelope()
    for name, value in oneof_branch.items():
        if value is None:
            getattr(msg, name).SetInParent()
        else:
            getattr(msg, name).CopyFrom(value)
    return wrap_gen2_ble_body(msg.SerializeToString())


def encode_get_device_status_info_plaintext() -> bytes:
    return _encode_ble_envelope(getDeviceStatusInfo=None)


def encode_get_battery_status_plaintext() -> bytes:
    return _encode_ble_envelope(getBatteryStatus=None)


def encode_get_device_info_plaintext() -> bytes:
    return _encode_ble_envelope(getDeviceInfo=None)


def normalize_gen2_message_for_status(msg: dict) -> dict:
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
    out = dict(base)
    for key, val in update.items():
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = deep_merge_partial_proto_dict(out[key], val)
        else:
            out[key] = val
    return out


def deep_merge_device_status_info(base: dict, update: dict) -> dict:
    merged = deep_merge_partial_proto_dict(base, update)
    if "wateringStatusSummary" not in update or "wateringStatus" in update:
        return merged
    sessions = (update.get("wateringStatusSummary") or {}).get("sessions") or []
    if sessions:
        return merged
    ds = short_gen2_enum_name(update.get("deviceStatus"))
    if not ds:
        ds = short_gen2_enum_name(merged.get("deviceStatus"))
    if ds in ("deviceIdle", "deviceOff"):
        merged.pop("wateringStatus", None)
    return merged


def merge_gen2_decoded(prev: dict | None, new: dict) -> dict:
    """Merge NOTIFY oneof branches so deviceInfo and deviceStatusInfo can coexist."""
    if not prev:
        return new
    prev_msg = prev.get("message") or {}
    new_msg = new.get("message") or {}
    merged_msg = {**prev_msg, **new_msg}
    for key in ("deviceInfo", "deviceStatusInfo"):
        prev_b = prev_msg.get(key)
        new_b = new_msg.get(key)
        if isinstance(prev_b, dict) and isinstance(new_b, dict):
            if key == "deviceStatusInfo":
                merged_msg[key] = deep_merge_device_status_info(prev_b, new_b)
            else:
                merged_msg[key] = deep_merge_partial_proto_dict(prev_b, new_b)
    out = {**new, "message": normalize_gen2_message_for_status(merged_msg)}
    out["_framing"] = new.get("_framing") or prev.get("_framing")
    return out


def parse_battery_percent_mv_from_decoded(decoded: dict | None) -> tuple[int | None, int | None]:
    """Read battery percent and voltage from deviceStatusInfo (or top-level batteryStatus)."""
    if not decoded:
        return None, None
    m = normalize_gen2_message_for_status(decoded.get("message") or {})
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
        except (TypeError, ValueError):
            pct = None

    mv: int | None = None
    if mv_raw is not None:
        try:
            mv = int(mv_raw)
        except (TypeError, ValueError):
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


def format_gen2_battery_line(message: dict | None) -> str | None:
    """Human-readable battery summary, e.g. ``72% (2833 mV)``."""
    if not message:
        return None
    pct, mv = parse_battery_percent_mv_from_decoded({"message": message})
    display_pct, _source = resolve_battery_percent_display(pct, mv)
    if display_pct is not None and mv is not None:
        return f"{display_pct}% ({mv} mV)"
    if display_pct is not None:
        return f"{display_pct}%"
    if mv is not None:
        return f"{mv} mV"
    return None


def normalize_num_stations(raw: int | None) -> int:
    """Clamp deviceInfo.numStations to known hose-timer port counts (1, 2, or 4)."""
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
    """Read deviceInfo.numStations from a decoded Gen 2 BLE message (if present)."""
    if not decoded:
        return None
    m = decoded.get("message") or {}
    di = m.get("deviceInfo") or {}
    n = di.get("numStations")
    if n is None:
        return None
    try:
        return normalize_num_stations(int(n))
    except (TypeError, ValueError):
        return None


def short_gen2_enum_name(value: Any) -> str:
    """Protobuf enum name → short value (DeviceStatus_deviceIdle → deviceIdle)."""
    if value is None:
        return ""
    s = str(value)
    if "_" in s:
        return s.rsplit("_", 1)[-1]
    return s


_STATION_FAULT_TYPE_KEYS: tuple[str, ...] = ("noFlow", "highFlow", "lowFlow")

_ACTIVE_WATERING_STATUSES: frozenset[str] = frozenset(
    {
        "wateringInProgress",
        "pumpDelay",
        "stationDelay",
    }
)

_ACTIVE_SWITCH_STATUSES: frozenset[str] = frozenset({"wateringInProgress"})


def parse_station_faults(decoded: dict | None, station_id: int) -> list[str]:
    if not decoded:
        return []
    m = normalize_gen2_message_for_status(decoded.get("message") or {})
    dsi = m.get("deviceStatusInfo") or {}
    fault_status = dsi.get("faultStatus") or {}
    faults: list[str] = []

    for entry in fault_status.get("stationFaults") or []:
        if not isinstance(entry, dict):
            continue
        try:
            sid = int(entry.get("stationId", -1))
        except (TypeError, ValueError):
            continue
        if sid != station_id:
            continue
        for key in _STATION_FAULT_TYPE_KEYS:
            if entry.get(key) is not None:
                faults.append(key)

    return faults


def _legacy_watering_applies_to_station(
    ws: dict[str, Any], station_id: int, *, num_stations: int
) -> bool:
    st_name = short_gen2_enum_name(ws.get("status"))
    if st_name not in _ACTIVE_WATERING_STATUSES:
        return False
    cur = ws.get("currentStationId")
    if cur is not None:
        try:
            return int(cur) == station_id
        except (TypeError, ValueError):
            return False
    return num_stations == 1 and station_id == 0


def _timer_mode_run_time_sec(dsi: dict[str, Any], station_id: int) -> int | None:
    tm = dsi.get("timerMode") or {}
    if short_gen2_enum_name(tm.get("mode")) != "manualMode":
        return None
    mmp = tm.get("manualModeParams") or {}
    for st in mmp.get("stationInfo") or []:
        if not isinstance(st, dict):
            continue
        try:
            sid = int(st.get("stationId", -1))
        except (TypeError, ValueError):
            continue
        if sid != station_id:
            continue
        raw = st.get("runTimeSec")
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    return None


def _manual_mode_targets_station(dsi: dict[str, Any], station_id: int) -> bool:
    tm = dsi.get("timerMode") or {}
    if short_gen2_enum_name(tm.get("mode")) != "manualMode":
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
    """Fallback when deviceStatus is wateringInProgress (lab status shape)."""
    if short_gen2_enum_name(dsi.get("deviceStatus")) != "wateringInProgress":
        return None
    ws = dsi.get("wateringStatus") or {}
    if (
        isinstance(ws, dict)
        and ws
        and _legacy_watering_applies_to_station(ws, station_id, num_stations=num_stations)
    ):
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
    m = normalize_gen2_message_for_status(decoded.get("message") or {})
    dsi = m.get("deviceStatusInfo") or {}
    has_summary = "wateringStatusSummary" in dsi
    sessions = (dsi.get("wateringStatusSummary") or {}).get("sessions") or []

    for sess in sessions:
        if not isinstance(sess, dict):
            continue
        try:
            cur = int(sess.get("currentStationId", -1))
        except (TypeError, ValueError):
            continue
        if cur == station_id:
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
        st_name = short_gen2_enum_name(sess.get("status"))
        out["watering_status"] = st_name or None
        rem = sess.get("currentTimeRemainingSec")
        if rem is not None:
            try:
                out["remaining_sec"] = int(rem)
            except (TypeError, ValueError):
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
    """Whether station_id is actively manual-watering (not idle or between-station delay)."""
    if not decoded:
        return None

    m = normalize_gen2_message_for_status(decoded.get("message") or {})
    dsi = m.get("deviceStatusInfo") or {}
    has_summary = "wateringStatusSummary" in dsi

    sess = _resolve_watering_session(
        decoded, station_id, num_stations=num_stations
    )
    if sess:
        st_name = short_gen2_enum_name(sess.get("status"))
        if not st_name:
            return None
        return st_name in _ACTIVE_SWITCH_STATUSES

    ds_name = short_gen2_enum_name(dsi.get("deviceStatus"))
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


def ingest_gen2_notify(pt: bytes, store: dict[str, Any]) -> None:
    """Merge one decoded NOTIFY oneof branch into a lab-client status store."""
    try:
        data = decode_gen2_ble_plaintext(pt)
    except Exception:
        return
    oneof = (data.get("_framing") or {}).get("oneof")
    if not oneof:
        return
    payload = (data.get("message") or {}).get(oneof)
    if payload is not None:
        store[oneof] = payload


def gen2_notify_store_port_state(store: dict[str, Any], station_id: int) -> str:
    """Per-port watering state from accumulated Gen2 NOTIFY branches (lab store shape)."""
    dsi = store.get("deviceStatusInfo")
    if not isinstance(dsi, dict):
        return "unknown"
    wss = dsi.get("wateringStatusSummary") or {}
    for sess in wss.get("sessions") or []:
        if not isinstance(sess, dict):
            continue
        try:
            cur = int(sess.get("currentStationId", -1))
        except (TypeError, ValueError):
            continue
        if cur == station_id:
            return str(sess.get("status") or "active")
    for ws in (dsi.get("wateringStatus"), store.get("wateringStatus")):
        if not isinstance(ws, dict) or not ws.get("status"):
            continue
        cur = ws.get("currentStationId")
        applies = False
        if cur is not None:
            try:
                applies = int(cur) == station_id
            except (TypeError, ValueError):
                applies = False
        elif station_id == 0:
            applies = True
        if applies:
            return str(ws.get("status"))
    if dsi.get("deviceStatus") == "wateringInProgress":
        tm = dsi.get("timerMode") or {}
        if tm.get("mode") == "manualMode":
            mmp = tm.get("manualModeParams") or {}
            for st in mmp.get("stationInfo") or []:
                if not isinstance(st, dict):
                    continue
                try:
                    if int(st.get("stationId", -1)) == station_id:
                        return "wateringInProgress"
                except (TypeError, ValueError):
                    continue
        if station_id == 0:
            return "wateringInProgress"
    return "idle"
