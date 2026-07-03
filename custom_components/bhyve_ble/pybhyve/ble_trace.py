"""Structured ATT trace lines for one BLE address."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from .constants import PairingHandleProfile

AttOp = Literal["write_req", "write_f", "read_rsp", "notify"]

_CHAR_HANDLE: dict[str, Callable[[PairingHandleProfile], str]] = {
    "network_char": lambda h: h.network_char,
    "aes_char": lambda h: h.aes_char,
    "write_char": lambda h: h.write_char,
    "notify_char": lambda h: h.notify_char,
}


def format_att_trace_line(
    mac: str,
    att_op: AttOp,
    char_role: str,
    handle: str,
    wire: bytes,
    *,
    plaintext: bytes | None = None,
    detail: str = "",
) -> list[str]:
    head = (
        f"[{mac.upper()}] {att_op:<9} {char_role:<13} @{handle}  "
        f"hex: {wire.hex()}"
    )
    lines = [head]
    if plaintext is not None:
        tail = f"  pt={plaintext.hex()}"
        if detail:
            tail = f"{tail}  |  {detail}"
        lines.append(f"  {tail.lstrip()}")
    elif detail:
        lines.append(f"  {detail}")
    return lines


def network_char_detail(wire: bytes) -> str:
    """Human hint for the 18-byte network_char provision blob."""
    if len(wire) < 18:
        return ""
    device_id_val = int.from_bytes(wire[0:2], "little")
    return f"prefix={wire[0:2].hex()} (device_id {device_id_val})  key={wire[2:18].hex()}"


class BleTraceReporter:
    """Emit ATT trace lines for a single device MAC."""

    def __init__(
        self,
        mac: str,
        handles: PairingHandleProfile,
        *,
        enabled: bool = True,
        emit: Callable[[str], None] | None = None,
    ) -> None:
        self.mac = mac.upper()
        self.handles = handles
        self.enabled = enabled
        self._emit = emit or print

    def _handle(self, char_role: str) -> str:
        try:
            return _CHAR_HANDLE[char_role](self.handles)
        except KeyError as exc:
            msg = f"unknown GATT role {char_role!r}"
            raise ValueError(msg) from exc

    def _att(
        self,
        att_op: AttOp,
        char_role: str,
        wire: bytes,
        *,
        plaintext: bytes | None = None,
        detail: str = "",
    ) -> None:
        if not self.enabled:
            return
        for line in format_att_trace_line(
            self.mac,
            att_op,
            char_role,
            self._handle(char_role),
            wire,
            plaintext=plaintext,
            detail=detail,
        ):
            self._emit(line)

    def write_req(
        self,
        char_role: str,
        wire: bytes,
        *,
        plaintext: bytes | None = None,
        detail: str = "",
    ) -> None:
        self._att("write_req", char_role, wire, plaintext=plaintext, detail=detail)

    def write_f(
        self,
        char_role: str,
        wire: bytes,
        *,
        plaintext: bytes | None = None,
        detail: str = "",
    ) -> None:
        self._att("write_f", char_role, wire, plaintext=plaintext, detail=detail)

    def read_rsp(
        self,
        char_role: str,
        wire: bytes,
        *,
        detail: str = "",
    ) -> None:
        self._att("read_rsp", char_role, wire, detail=detail)

    def notify(
        self,
        wire: bytes,
        *,
        plaintext: bytes | None = None,
        detail: str = "",
    ) -> None:
        self._att("notify", "notify_char", wire, plaintext=plaintext, detail=detail)
