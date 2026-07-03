"""
Optional debug logging for Orbit BLE plaintext and decoded payloads.

Enable in Home Assistant configuration.yaml
    logger:
      logs:
        custom_components.bhyve_ble.logging: debug
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .pybhyve.ble_trace import BleTraceReporter, network_char_detail
from .pybhyve.constants import GEN1_HANDLES

from .pybhyve.gen2_codec import decode_gen2_ble_plaintext

_LOG = logging.getLogger(__name__)

_MAX_JSON_CHARS = 12_000


def _debug(address: str, msg: str, *args: object) -> None:
    _LOG.debug("[%s] " + msg, address, *args)


def _gen1_att_trace(address: str) -> BleTraceReporter | None:
    if not _LOG.isEnabledFor(logging.DEBUG):
        return None
    return BleTraceReporter(
        address,
        GEN1_HANDLES,
        emit=lambda line: _LOG.debug("%s", line),
    )


def log_ble_att_write_req(
    address: str,
    char_role: str,
    wire: bytes,
    *,
    detail: str = "",
) -> None:
    trace = _gen1_att_trace(address)
    if trace is not None:
        trace.write_req(char_role, wire, detail=detail)


def log_ble_att_write_f(
    address: str,
    wire: bytes,
    *,
    plaintext: bytes | None = None,
    detail: str = "",
) -> None:
    trace = _gen1_att_trace(address)
    if trace is not None:
        trace.write_f("write_char", wire, plaintext=plaintext, detail=detail)


def log_ble_att_read_rsp(address: str, char_role: str, wire: bytes, *, detail: str = "") -> None:
    trace = _gen1_att_trace(address)
    if trace is not None:
        trace.read_rsp(char_role, wire, detail=detail)


def log_ble_att_notify(
    address: str,
    wire: bytes,
    *,
    plaintext: bytes | None = None,
    detail: str = "",
) -> None:
    trace = _gen1_att_trace(address)
    if trace is not None:
        trace.notify(wire, plaintext=plaintext, detail=detail)


def log_ble_att_network_char(address: str, wire: bytes) -> None:
    trace = _gen1_att_trace(address)
    if trace is not None:
        trace.write_req("network_char", wire, detail=network_char_detail(wire))


def _oneof_from_plaintext(plaintext: bytes) -> str | None:
    try:
        decoded = decode_gen2_ble_plaintext(plaintext)
    except Exception:  # noqa: BLE001
        return None
    return (decoded.get("_framing") or {}).get("oneof")


def _format_json(obj: Any) -> str:
    text = json.dumps(obj, indent=2, sort_keys=True, default=str)
    if len(text) > _MAX_JSON_CHARS:
        return text[:_MAX_JSON_CHARS] + f"\n… ({len(text) - _MAX_JSON_CHARS} chars truncated)"
    return text


def _packet_summary(link_msg_type: int, plaintext: bytes, *, oneof: str | None = None) -> str:
    label = oneof if oneof is not None else _oneof_from_plaintext(plaintext) or "?"
    return (
        f"link_type=0x{link_msg_type:02x} oneof={label} "
        f"plaintext_len={len(plaintext)} plaintext_hex={plaintext.hex()}"
    )


def log_ble_tx(address: str, link_msg_type: int, plaintext: bytes) -> None:
    _debug(address, "TX %s", _packet_summary(link_msg_type, plaintext))


def log_ble_rx(
    address: str,
    link_msg_type: int,
    plaintext: bytes,
    decoded: dict[str, Any],
) -> None:
    oneof = (decoded.get("_framing") or {}).get("oneof")
    _debug(
        address,
        "RX %s decoded_json:\n%s",
        _packet_summary(link_msg_type, plaintext, oneof=oneof),
        _format_json(decoded),
    )


def log_ble_rx_decode_failed(
    address: str, link_msg_type: int, plaintext: bytes, err: Exception
) -> None:
    _debug(address, "RX decode failed %s err=%s", _packet_summary(link_msg_type, plaintext), err)


def log_ble_merged(address: str, merged: dict[str, Any] | None) -> None:
    if not merged:
        _debug(address, "merged last_message empty")
        return
    msg = merged.get("message") or {}
    framing = merged.get("_framing") or {}
    _debug(
        address,
        "merged last_message oneof_last=%s message_keys=%s merged_json:\n%s",
        framing.get("oneof") or "?",
        sorted(msg.keys()),
        _format_json(merged),
    )
