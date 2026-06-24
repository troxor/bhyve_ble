"""Tests for per-device vs shared network key resolution."""

from __future__ import annotations

import base64

import pytest
from bhyve_ble.const import (
    CONF_DEVICE_GENERATION,
    CONF_DEVICE_NETWORK_KEY_B64,
    CONF_DEVICES,
    CONF_MESH_DEVICE_ID,
    CONF_NETWORK_KEY_B64,
    GENERATION_GEN1,
    GENERATION_GEN2,
)
from bhyve_ble.device_credentials import device_network_key, mesh_device_id
from bhyve_ble.gen1_discovery import mesh_id_from_manufacturer_data
from bhyve_ble.provisioning import build_network_char_payload


class _FakeEntry:
    def __init__(self, data: dict) -> None:
        self.data = data


def test_gen2_uses_entry_network_key() -> None:
    shared = bytes(range(16))
    entry = _FakeEntry(
        {
            CONF_NETWORK_KEY_B64: base64.b64encode(shared).decode(),
            CONF_DEVICES: {"AA:BB:CC:DD:EE:FF": {CONF_DEVICE_GENERATION: GENERATION_GEN2}},
        }
    )
    assert device_network_key(entry, "AA:BB:CC:DD:EE:FF") == shared
    assert mesh_device_id(entry, "AA:BB:CC:DD:EE:FF") is None


def test_gen1_uses_per_device_key_and_mesh_id() -> None:
    shared = bytes(range(16))
    device_key = bytes([0xAB] * 16)
    entry = _FakeEntry(
        {
            CONF_NETWORK_KEY_B64: base64.b64encode(shared).decode(),
            CONF_DEVICES: {
                "44:67:55:17:D8:4C": {
                    CONF_DEVICE_GENERATION: GENERATION_GEN1,
                    CONF_DEVICE_NETWORK_KEY_B64: base64.b64encode(device_key).decode(),
                    CONF_MESH_DEVICE_ID: 1806,
                }
            },
        }
    )
    assert device_network_key(entry, "44:67:55:17:D8:4C") == device_key
    assert mesh_device_id(entry, "44:67:55:17:D8:4C") == 1806


def test_gen1_missing_device_key_raises() -> None:
    entry = _FakeEntry(
        {
            CONF_NETWORK_KEY_B64: base64.b64encode(bytes(16)).decode(),
            CONF_DEVICES: {
                "44:67:55:17:D8:4C": {CONF_DEVICE_GENERATION: GENERATION_GEN1},
            },
        }
    )
    with pytest.raises(ValueError, match="device_network_key_b64"):
        device_network_key(entry, "44:67:55:17:D8:4C")


def test_build_network_char_gen1_prefix() -> None:
    key = bytes([0x11] * 16)
    payload = build_network_char_payload(key, mesh_device_id=1806)
    assert payload[0:2] == (1806).to_bytes(2, "little")
    assert payload[2:] == key


def test_build_network_char_gen2_prefix() -> None:
    key = bytes([0x22] * 16)
    payload = build_network_char_payload(key)
    assert payload[0:2] == b"\x01\x00"
    assert payload[2:] == key


def test_mesh_id_from_manufacturer_data() -> None:
    from bhyve_ble.gen1_discovery import mesh_id_from_manufacturer_blob

    assert mesh_id_from_manufacturer_data({0xFFFF: b"\x7c\x82\xaa"}) == 33404
    assert mesh_id_from_manufacturer_data({0xFFFF: b"\x0e\x07\xaa"}) == 1806
    assert mesh_id_from_manufacturer_data({0xFFFF: b"\x06\x00\x08\x80"}) == 32776
    assert mesh_id_from_manufacturer_data({0xFFFF: b"\x06\x00\x7c\x82"}) == 33404
    assert mesh_id_from_manufacturer_data({0xFFFF: b"\x06\x00"}) is None
    assert mesh_id_from_manufacturer_data({0xFFFF: b"\x00\x00"}) is None
    assert mesh_id_from_manufacturer_data({0x004C: b"\x0e\x07"}) is None
    assert mesh_id_from_manufacturer_data({}) is None
    assert mesh_id_from_manufacturer_blob(b"\x06\x00") is None
