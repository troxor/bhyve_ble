from __future__ import annotations

import base64

import pytest
from bhyve_ble.network_key import parse_network_key, parse_or_generate_network_key

_FAKE_KEY_HEX = "7f3c9e1a42b8d605f2e8c4a19d3760bf"


def test_generate_when_empty() -> None:
    a = parse_or_generate_network_key("")
    b = parse_or_generate_network_key("   ")
    assert len(a) == 16
    assert len(b) == 16


def test_hex_32() -> None:
    assert parse_network_key(_FAKE_KEY_HEX) == bytes.fromhex(_FAKE_KEY_HEX)


def test_hex_and_base64() -> None:
    raw = bytes.fromhex(_FAKE_KEY_HEX)
    assert parse_network_key(_FAKE_KEY_HEX) == raw
    assert parse_network_key(base64.b64encode(raw).decode()) == raw


def test_parse_network_key_rejects_loose_base64() -> None:
    raw = bytes.fromhex(_FAKE_KEY_HEX)
    b64 = base64.b64encode(raw).decode()
    with pytest.raises(ValueError, match="Invalid network key"):
        parse_network_key(b64.rstrip("="))
    with pytest.raises(ValueError, match="Invalid network key"):
        parse_network_key(b64[:10] + "\n" + b64[10:])


def test_parse_network_key_rejects_empty() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        parse_network_key("  ")


def test_parse_network_key_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="Invalid network key"):
        parse_network_key("not-a-key")
