from __future__ import annotations

import base64

import pytest
from bhyve_ble.pybhyve.gen1_ops import gen1_network_key

_FAKE_KEY_HEX = "7f3c9e1a42b8d605f2e8c4a19d3760bf"


def test_generate_when_empty() -> None:
    a = gen1_network_key("")
    b = gen1_network_key("   ")
    c = gen1_network_key(None)
    assert len(a) == 16
    assert len(b) == 16
    assert len(c) == 16


def test_hex_32() -> None:
    assert gen1_network_key(_FAKE_KEY_HEX) == bytes.fromhex(_FAKE_KEY_HEX)


def test_hex_and_base64() -> None:
    raw = bytes.fromhex(_FAKE_KEY_HEX)
    assert gen1_network_key(_FAKE_KEY_HEX) == raw
    assert gen1_network_key(base64.b64encode(raw).decode()) == raw


def test_gen1_network_key_rejects_loose_base64() -> None:
    raw = bytes.fromhex(_FAKE_KEY_HEX)
    b64 = base64.b64encode(raw).decode()
    with pytest.raises(ValueError, match="Invalid network key"):
        gen1_network_key(b64.rstrip("="))
    with pytest.raises(ValueError, match="Invalid network key"):
        gen1_network_key(b64[:10] + "\n" + b64[10:])


def test_gen1_network_key_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="Invalid network key"):
        gen1_network_key("not-a-key")
