"""CLI human-readable decode and status summaries (not used by HA integration)."""

from __future__ import annotations

import json
from typing import Any

from ..constants import Generation
from ..gen1_codec import (
    GEN1_ASYNC_NOTIFY_CMDS,
    GEN1_MODEL,
    GEN1_RESPONSE_BIT,
    decode_gen1_inner_payload,
    decode_gen1_plaintext,
    gen1_status_snapshot_verified,
    parse_gen1_battery_percent_mv,
    parse_gen1_station_status,
)
from ..gen2_codec import (
    MAGIC_BYTES,
    decode_gen2_ble_plaintext,
    format_gen2_battery_line,
    gen2_notify_store_port_state,
    resolve_battery_percent_display,
)


def format_gen1_inner_status(status: dict[str, Any]) -> str:
    """One-line human summary for decode_gen1_inner_payload."""
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
            return f"fault: battery={pct}% ({status['battery_mv']} mV)"
        return "fault"
    if kind == "battery":
        return f"{status['battery_percent']}% ({status['battery_mv']} mV)"
    if kind == "probe_reply":
        return f"status probe reply: value={status.get('value')}"
    if kind == "manual_start_ack":
        return f"manual start acknowledged: {status.get('duration_sec')}s"
    return f"gen1 inner: {kind}"


def format_gen1_decoded(
    decoded: dict[str, Any],
    *,
    att_direction: str | None = None,
    include_hex: bool = True,
) -> str:
    cmd = decoded["cmd_hex"]
    cmd_int = int(decoded["cmd"])
    tail = str(decoded["payload_hex"])
    if include_hex:
        if len(tail) > 40:
            tail = tail[:40] + "..."
        hex_part = f"  {tail}"
    else:
        hex_part = ""

    if decoded.get("request_cmd_hex"):
        role = f"response {decoded['request_cmd_hex']}->{cmd}"
    elif att_direction == "write_f":
        role = f"write {cmd}"
    elif att_direction == "notify":
        if cmd_int in GEN1_ASYNC_NOTIFY_CMDS and not (cmd_int & GEN1_RESPONSE_BIT):
            role = f"push {cmd}"
        else:
            role = f"notify {cmd}"
    elif cmd_int & GEN1_RESPONSE_BIT:
        req = decoded.get("request_cmd_hex")
        role = f"response {req}->{cmd}" if req else f"response {cmd}"
    elif cmd_int in GEN1_ASYNC_NOTIFY_CMDS:
        role = f"push {cmd}"
    else:
        role = f"cmd {cmd}"

    return f"gen1 {role}{hex_part}"


_STATUS_LABEL_WIDTH = 12


def _print_status_field(label: str, value: str) -> None:
    print(f"  {label + ':':{_STATUS_LABEL_WIDTH}}{value}")


def _map_gen1_state(raw: str) -> str:
    return {
        "watering": "wateringInProgress",
        "off": "idle",
        "fault": "fault",
        "unknown": "unknown",
    }.get(raw, raw)


def _gen1_port_state(snapshot: dict[str, dict[str, Any]], station_id: int | None) -> str:
    sid = 0 if station_id is None else station_id
    return _map_gen1_state(parse_gen1_station_status(snapshot, sid)["state"])


def _gen1_state_detail(snapshot: dict[str, dict[str, Any]]) -> str | None:
    ws = snapshot.get("watering_status")
    if not isinstance(ws, dict) or not ws.get("active"):
        return None
    parts: list[str] = []
    rem = ws.get("remaining_sec")
    tot = ws.get("total_sec")
    if rem is not None:
        parts.append(f"remaining_sec={rem}")
    if tot is not None:
        parts.append(f"total_sec={tot}")
    return "  ".join(parts) if parts else None


def _format_gen1_battery_line(snapshot: dict[str, dict[str, Any]]) -> str | None:
    pct, mv = parse_gen1_battery_percent_mv(snapshot)
    display_pct, _source = resolve_battery_percent_display(pct, mv)
    if display_pct is not None and mv is not None:
        return f"{display_pct}% ({mv} mV)"
    if display_pct is not None:
        return f"{display_pct}%"
    if mv is not None:
        return f"{mv} mV"
    return None


def _gen2_port_state(store: dict[str, Any], station_id: int | None) -> str:
    if station_id is not None:
        return gen2_notify_store_port_state(store, station_id)
    dsi = store.get("deviceStatusInfo")
    if isinstance(dsi, dict):
        ds = dsi.get("deviceStatus")
        if ds:
            return str(ds)
    return "unknown"


def _gen2_state_detail(store: dict[str, Any]) -> str | None:
    dsi = store.get("deviceStatusInfo")
    sources: list[dict[str, Any]] = []
    if isinstance(dsi, dict):
        ws = dsi.get("wateringStatus")
        if isinstance(ws, dict):
            sources.append(ws)
    ws_top = store.get("wateringStatus")
    if isinstance(ws_top, dict):
        sources.append(ws_top)
    for ws in sources:
        if not ws.get("status"):
            continue
        parts = [f"status={ws['status']}"]
        if ws.get("currentStationId") is not None:
            parts.append(f"station={ws['currentStationId']}")
        if ws.get("currentTimeRemainingSec") is not None:
            parts.append(f"remaining_sec={ws['currentTimeRemainingSec']}")
        if ws.get("totalRunTimeSec") is not None:
            parts.append(f"total_sec={ws['totalRunTimeSec']}")
        return "  ".join(parts)
    return None


def _gen2_battery_line_from_store(store: dict[str, Any]) -> str | None:
    msg: dict[str, Any] = {}
    dsi = store.get("deviceStatusInfo")
    if isinstance(dsi, dict):
        msg["deviceStatusInfo"] = dsi
    bat = store.get("batteryStatus")
    if isinstance(bat, dict):
        msg["batteryStatus"] = bat
    return format_gen2_battery_line(msg)


def _gen2_fault_summary(store: dict[str, Any]) -> str | None:
    fs = store.get("faultStatus")
    if not isinstance(fs, dict) or not fs:
        dsi = store.get("deviceStatusInfo")
        if isinstance(dsi, dict):
            nested = dsi.get("faultStatus")
            if isinstance(nested, dict):
                fs = nested
    if not isinstance(fs, dict) or not fs:
        return None
    bool_fields = [
        ("valveOnNoFlowDetected", "VALVE ON but NO FLOW detected"),
        ("valveOffFlowDetected", "VALVE OFF but FLOW detected"),
        ("valveLowFlowDetected", "LOW FLOW detected"),
        ("valveHighFlowDetected", "HIGH FLOW detected"),
    ]
    hits = [label for key, label in bool_fields if fs.get(key) is True]
    if hits:
        return "; ".join(hits)
    station_faults = fs.get("stationFaults")
    if isinstance(station_faults, list) and station_faults:
        return f"{len(station_faults)} station fault(s)"
    return None


def _print_gen1_status_summary(
    snapshot: dict[str, dict[str, Any]],
    station_id: int | None,
) -> None:
    if not gen1_status_snapshot_verified(snapshot):
        print("  (no status NOTIFY decoded)")
        return

    _print_status_field("State", _gen1_port_state(snapshot, station_id))
    detail = _gen1_state_detail(snapshot)
    if detail:
        _print_status_field("detail", detail)

    device_info = snapshot.get("device_info")
    if isinstance(device_info, dict):
        if device_info.get("num_stations") is not None:
            _print_status_field("stations", str(device_info["num_stations"]))
        if device_info.get("firmware_version"):
            _print_status_field("firmware", str(device_info["firmware_version"]))
    _print_status_field("hardware", GEN1_MODEL)

    battery_line = _format_gen1_battery_line(snapshot)
    if battery_line:
        _print_status_field("battery", battery_line)

    fault = snapshot.get("fault")
    if isinstance(fault, dict) and fault.get("kind") == "fault" and not fault.get("battery_mv"):
        _print_status_field("faults", "fault")


def _print_gen2_status_summary(
    store: dict[str, Any],
    station_id: int | None,
) -> None:
    if not store:
        print("  (no status NOTIFY decoded)")
        return

    _print_status_field("State", _gen2_port_state(store, station_id))
    detail = _gen2_state_detail(store)
    if detail:
        _print_status_field("detail", detail)

    di = store.get("deviceInfo")
    if isinstance(di, dict) and di:
        if di.get("numStations") is not None:
            _print_status_field("stations", str(di["numStations"]))
        if di.get("fwVersion"):
            _print_status_field("firmware", str(di["fwVersion"]))
        if di.get("hwVersion"):
            _print_status_field("hardware", str(di["hwVersion"]))

    battery_line = _gen2_battery_line_from_store(store)
    if battery_line:
        _print_status_field("battery", battery_line)

    fault_line = _gen2_fault_summary(store)
    if fault_line:
        _print_status_field("faults", fault_line)


def print_device_status_summary(
    *,
    generation: Generation,
    gen1_snapshot: dict[str, dict[str, Any]],
    gen2_store: dict[str, Any],
    station_id: int | None = None,
) -> None:
    print("\n=== Device status ===")
    if station_id is not None:
        print(f"  Port {station_id + 1}")
    if generation == "gen1":
        _print_gen1_status_summary(gen1_snapshot, station_id)
    else:
        _print_gen2_status_summary(gen2_store, station_id)
    print("=====================\n")


def _gen2_summarize_device_status_info(dsi: dict, lines: list[str]) -> None:
    ds = dsi.get("deviceStatus")
    if ds:
        lines.append(f"   State:        {ds}")
    tm = dsi.get("timerMode")
    if isinstance(tm, dict):
        mode = tm.get("mode")
        if mode:
            lines.append(f"   timerMode:    {mode}")
        mmp = tm.get("manualModeParams")
        if isinstance(mmp, dict):
            for i, st in enumerate(mmp.get("stationInfo") or []):
                if not isinstance(st, dict):
                    continue
                sid = st.get("stationId")
                rts = st.get("runTimeSec")
                lines.append(f"   station[{i}]:  id={sid}  runTimeSec={rts}")
                if i >= 3:
                    break
    ws = dsi.get("wateringStatus")
    if isinstance(ws, dict) and any(ws.values()):
        lines.append(_gen2_format_watering_line(ws))
    battery_line = format_gen2_battery_line({"deviceStatusInfo": dsi})
    if battery_line:
        lines.append(f"   battery:      {battery_line}")


def _gen2_format_watering_line(ws: dict) -> str:
    parts: list[str] = []
    status = ws.get("status")
    if status:
        parts.append(f"status={status}")
    st = ws.get("currentStationId")
    if st is not None:
        parts.append(f"station={st}")
    rem = ws.get("currentTimeRemainingSec")
    if rem is not None:
        parts.append(f"remaining_sec={rem}")
    tot = ws.get("totalRunTimeSec")
    if tot is not None:
        parts.append(f"total_sec={tot}")
    return f"   detail:       {'  '.join(parts)}"


def _gen2_summarize_fault_status(fs: dict, lines: list[str]) -> None:
    bool_fields = [
        ("valveOnNoFlowDetected", "VALVE ON but NO FLOW detected"),
        ("valveOffFlowDetected", "VALVE OFF but FLOW detected (unscheduled flow)"),
        ("valveLowFlowDetected", "LOW FLOW detected"),
        ("valveHighFlowDetected", "HIGH FLOW detected"),
    ]
    hits = [label for k, label in bool_fields if fs.get(k) is True]
    if hits:
        lines.append("   faults:       " + "; ".join(hits))
    station_faults = fs.get("stationFaults")
    if isinstance(station_faults, list) and station_faults:
        snippet = json.dumps({"stationFaults": station_faults}, indent=2, ensure_ascii=False)
        lines.append("   stationFaults:")
        lines.extend("   | " + ln for ln in snippet.splitlines()[:24])
        if len(snippet.splitlines()) > 24:
            lines.append("   | ...")


def _gen2_append_decode_block(pt: bytes, lines: list[str]) -> None:
    if len(pt) < 8:
        lines.append("   gen2 decode: (too short)")
        return
    try:
        data = decode_gen2_ble_plaintext(pt)
    except Exception as e:
        lines.append(f"   gen2 decode: (failed: {e})")
        return
    fm = data.get("_framing") or {}
    lines.append("   --- Gen 2 BLE protobuf (BleEnvelope) ---")
    crc = int(fm.get("wireChecksumUInt16LE") or 0)
    lines.append(
        f"   wire:         u16 inner_len={fm.get('innerLengthField')}  "
        f"protobuf_bytes={fm.get('protobufBytes')}  crc_u16=0x{crc:04x}"
    )
    oneof = fm.get("oneof")
    if oneof:
        lines.append(f"   oneof:        {oneof}")
    msg = data.get("message") or {}
    proto_id = msg.get("id")
    if isinstance(proto_id, (bytes, bytearray)) and len(proto_id) == 6:
        mac_s = ":".join(f"{b:02x}" for b in bytes(proto_id))
        lines.append(
            f"   proto id:     {mac_s}  (BleEnvelope field 1 `id`, 6 B)"
        )
    ts = msg.get("timestampSecEpochUTC")
    if ts not in (None, 0, ""):
        lines.append(f"   timestampSecEpochUTC: {ts}  (protobuf field 7)")
    branch = oneof
    payload = msg.get(branch) if branch and isinstance(msg.get(branch), dict) else None
    if branch == "deviceStatusInfo" and isinstance(payload, dict):
        _gen2_summarize_device_status_info(payload, lines)
    elif branch == "wateringStatus" and isinstance(payload, dict):
        lines.append(_gen2_format_watering_line(payload))
    elif branch == "faultStatus" and isinstance(payload, dict):
        if payload:
            _gen2_summarize_fault_status(payload, lines)
        else:
            lines.append("   faultStatus:  (empty)")
    if branch and branch not in ("deviceStatusInfo", "wateringStatus", "faultStatus"):
        pl = msg.get(branch)
        if isinstance(pl, dict) and pl:
            snippet = json.dumps(pl, indent=2, ensure_ascii=False)
            lines.append(f"   {branch}:")
            lines.extend("   | " + ln for ln in snippet.splitlines()[:32])
            if len(snippet.splitlines()) > 32:
                lines.append("   | ...")


def brief_gen2_plaintext(pt: bytes) -> str:
    if len(pt) < 4 or pt[:4] != MAGIC_BYTES:
        return "gen2 (not aa775a0f)"
    try:
        data = decode_gen2_ble_plaintext(pt)
    except Exception:
        return "gen2 (decode failed)"
    oneof = (data.get("_framing") or {}).get("oneof") or "?"
    msg = data.get("message") or {}
    parts = [f"gen2 {oneof}"]
    payload = msg.get(oneof) if isinstance(oneof, str) else None
    if not isinstance(payload, dict):
        return parts[0]
    if oneof == "wateringStatus":
        st = payload.get("status")
        rem = payload.get("currentTimeRemainingSec")
        if st:
            parts.append(str(st))
        if rem is not None:
            parts.append(f"rem={rem}s")
    elif oneof == "deviceStatusInfo":
        if payload.get("deviceStatus"):
            parts.append(str(payload["deviceStatus"]))
        ws = payload.get("wateringStatus") or {}
        if isinstance(ws, dict) and ws.get("status"):
            rem = ws.get("currentTimeRemainingSec")
            seg = str(ws["status"])
            if rem is not None:
                seg += f" rem={rem}s"
            parts.append(seg)
        battery_line = format_gen2_battery_line({"deviceStatusInfo": payload})
        if battery_line:
            parts.append(battery_line)
    elif oneof == "faultStatus" and payload:
        fault_labels = [
            ("valveOnNoFlowDetected", "VALVE ON but NO FLOW detected"),
            ("valveOffFlowDetected", "VALVE OFF but FLOW detected"),
            ("valveLowFlowDetected", "LOW FLOW detected"),
            ("valveHighFlowDetected", "HIGH FLOW detected"),
        ]
        hits = [label for key, label in fault_labels if payload.get(key) is True]
        if hits:
            parts.append("; ".join(hits))
    return " · ".join(parts)


def brief_gen1_plaintext(
    pt: bytes,
    *,
    att_direction: str,
) -> str:
    dec = decode_gen1_plaintext(pt)
    head = format_gen1_decoded(dec, att_direction=att_direction, include_hex=False)
    payload = bytes.fromhex(dec["payload_hex"])
    inner = decode_gen1_inner_payload(payload, direction=att_direction)
    if inner:
        return f"{head}  ·  {format_gen1_inner_status(inner)}"
    return head


def print_gatt_plaintext(
    direction: str,
    handle: str,
    pt: bytes,
    *,
    generation: Generation,
    verbose: int,
    step: str | None = None,
    wire: bytes | None = None,
    ctr_extra: str = "",
) -> None:
    if verbose < 1:
        return
    head = f"{direction} @{handle}"
    if step:
        head = f"{head}  —  {step}"
    print(head)
    if verbose >= 2:
        if wire is not None:
            print(f"  wire: {wire.hex()}{ctr_extra}")
        else:
            print(f"  hex: {pt.hex()}{ctr_extra}")
    try:
        if generation == "gen1":
            brief = brief_gen1_plaintext(
                pt,
                att_direction="write_f" if direction == "WRITE" else "notify",
            )
        else:
            brief = brief_gen2_plaintext(pt)
    except Exception:
        brief = "decode failed"
    print(f"  {brief}")


def format_gen1_verbose(pt: bytes, _verbose: int, *, skip_hex: bool = False) -> str:
    lines = ["   --- gen1 plaintext ---"]
    try:
        dec = decode_gen1_plaintext(pt)
    except Exception as e:
        lines.append(f"   decode failed: {e}")
        lines.append("   ---")
        return "\n".join(lines)
    if not skip_hex:
        lines.append(f"   plaintext_hex={pt.hex()}")
    lines.append(f"   {format_gen1_decoded(dec, att_direction='write_f')}")
    try:
        inner = decode_gen1_inner_payload(bytes.fromhex(dec["payload_hex"]))
        if inner is not None:
            lines.append(f"   inner:        {format_gen1_inner_status(inner)}")
    except Exception:
        pass
    lines.append("   ---")
    return "\n".join(lines)


def format_gen2_protobuf_only(pt: bytes) -> str:
    lines: list[str] = ["   --- Gen 2 protobuf ---"]
    _gen2_append_decode_block(pt, lines)
    lines.append("   ---")
    return "\n".join(lines)


def format_plaintext_readable(pt: bytes) -> str:
    lines: list[str] = [
        "   --- plaintext (readable) ---",
        f"   length:       {len(pt)} B",
    ]
    if len(pt) < 4:
        lines.append("   (too short for b-hyve envelope)")
        lines.append("   ---")
        return "\n".join(lines)
    if pt[:4] != MAGIC_BYTES:
        lines.append(f"   lead_in:      {pt[:min(8, len(pt))].hex()}...  (not aa775a0f)")
        lines.append("   ---")
        return "\n".join(lines)
    lines.append("   magic:        aa775a0f")
    if len(pt) >= 6:
        inner_le = int.from_bytes(pt[4:6], "little")
        lines.append(f"   u16 [4:6]:    0x{inner_le:04x} ({inner_le})")
    _gen2_append_decode_block(pt, lines)
    lines.append("   ---")
    return "\n".join(lines)
