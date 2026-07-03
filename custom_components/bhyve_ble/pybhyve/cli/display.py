"""CLI human-readable decode and status summaries (not used by HA integration)."""

from __future__ import annotations

import json
from typing import Any

from ..gen1_codec import (
    GEN1_ASYNC_NOTIFY_CMDS,
    GEN1_MODEL,
    GEN1_RESPONSE_BIT,
    decode_gen1_inner_payload,
    decode_gen1_plaintext,
)
from ..gen2_codec import (
    MAGIC_BYTES,
    decode_gen2_ble_plaintext,
    gen2_notify_store_port_state,
)
from ..constants import Generation


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
        record = gen1_snapshot.get("watering_status") or gen1_snapshot.get("watering_idle")
        watering = format_gen1_inner_status(record) if isinstance(record, dict) else None
        if watering:
            print(f"  watering:   {watering}")
        else:
            print("  watering:   (no status NOTIFY decoded)")
        print(f"  model:      {GEN1_MODEL}")
        device_info = gen1_snapshot.get("device_info")
        if device_info:
            print(f"  firmware:   {device_info['firmware_version']}")
            print(f"  stations:   {device_info['num_stations']}")
        fault = gen1_snapshot.get("fault")
        battery = gen1_snapshot.get("battery")
        if battery:
            print(
                f"  battery:    {battery['battery_percent']}% "
                f"({battery['battery_mv']} mV)"
            )
        elif fault and fault.get("battery_mv"):
            print(
                f"  battery:    {fault.get('battery_percent')}% "
                f"({fault['battery_mv']} mV)"
            )
    else:
        if station_id is not None:
            print(f"  State:   {gen2_notify_store_port_state(gen2_store, station_id)}")
        dsi = gen2_store.get("deviceStatusInfo")
        if isinstance(dsi, dict):
            detail_lines: list[str] = []
            _gen2_summarize_device_status_info(dsi, detail_lines)
            for line in detail_lines:
                print(line.replace("   ", "  ", 1) if line.startswith("   ") else f"  {line}")
        di = gen2_store.get("deviceInfo")
        if isinstance(di, dict) and di:
            if di.get("numStations") is not None:
                print(f"  stations:     {di['numStations']}")
            if di.get("fwVersion"):
                print(f"  firmware:     {di['fwVersion']}")
            if di.get("hwVersion"):
                print(f"  hardware:     {di['hwVersion']}")
            if not any(di.get(k) for k in ("numStations", "fwVersion", "hwVersion")):
                snippet = json.dumps(di, ensure_ascii=False)
                print(f"  deviceInfo:   {snippet[:200]}{'...' if len(snippet) > 200 else ''}")
        bat = gen2_store.get("batteryStatus")
        if isinstance(bat, dict) and bat and not (
            isinstance(dsi, dict) and isinstance(dsi.get("batteryStatus"), dict) and dsi["batteryStatus"]
        ):
            mv = bat.get("batteryLevelMV")
            if mv:
                print(f"  battery:      {bat.get('state')}  {mv} mV")
        fs = gen2_store.get("faultStatus")
        if isinstance(fs, dict) and fs:
            fault_lines: list[str] = []
            _gen2_summarize_fault_status(fs, fault_lines)
            for line in fault_lines:
                print(line.replace("   ", "  ", 1) if line.startswith("   ") else f"  {line}")
        ws = gen2_store.get("wateringStatus")
        if isinstance(ws, dict) and ws and not (
            isinstance(dsi, dict) and isinstance(dsi.get("wateringStatus"), dict) and dsi["wateringStatus"]
        ):
            line = _gen2_format_watering_line(ws).replace("   ", "  ", 1)
            print(line if line.startswith("  ") else f"  {line}")
        if not gen2_store:
            print("  (no status NOTIFY decoded)")
    print("=====================\n")


def _gen2_summarize_device_status_info(dsi: dict, lines: list[str]) -> None:
    ds = dsi.get("deviceStatus")
    if ds:
        lines.append(f"   deviceStatus: {ds}")
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
    bat = dsi.get("batteryStatus")
    if isinstance(bat, dict) and bat.get("batteryLevelMV"):
        lines.append(f"   battery:      {bat.get('state')}  {bat.get('batteryLevelMV')} mV")


def _gen2_format_watering_line(ws: dict) -> str:
    return (
        "   watering:     status={status}  station={st}"
        "  remaining_sec={rem}  total_sec={tot}".format(
            status=ws.get("status"),
            st=ws.get("currentStationId"),
            rem=ws.get("currentTimeRemainingSec"),
            tot=ws.get("totalRunTimeSec"),
        )
    )


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
        bat = payload.get("batteryStatus") or {}
        if isinstance(bat, dict) and bat.get("batteryLevelMV"):
            parts.append(f"{bat['batteryLevelMV']} mV")
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


def format_gen1_verbose(pt: bytes, verbose: int, *, skip_hex: bool = False) -> str:
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
