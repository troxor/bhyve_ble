from __future__ import annotations

import base64

from bhyve_ble.network_key import parse_or_generate_network_key


def test_generate_when_empty() -> None:
    a = parse_or_generate_network_key("")
    b = parse_or_generate_network_key("   ")
    assert len(a) == 16
    assert len(b) == 16


def test_hex_32() -> None:
    hx = "0123456789abcdef0123456789abcdef"
    assert parse_or_generate_network_key(hx) == bytes.fromhex(hx)


def test_base64() -> None:
    raw = bytes(range(16))
    assert parse_or_generate_network_key(base64.b64encode(raw).decode()) == raw
