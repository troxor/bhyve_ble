from __future__ import annotations

import base64

from bhyve_ble.const import (
    CONF_DEVICE_GENERATION,
    CONF_DEVICES,
    CONF_NETWORK_KEY_B64,
    GENERATION_GEN1,
    GENERATION_GEN2,
)
from bhyve_ble.entry_data import (
    entry_gen2_pairing_locked,
    entry_has_gen2_device,
    entry_has_network_key,
    entry_needs_network_key_prompt,
)


def test_entry_needs_network_key_prompt_empty() -> None:
    assert entry_needs_network_key_prompt(None) is True
    assert entry_needs_network_key_prompt({}) is True


def test_entry_needs_network_key_prompt_with_key() -> None:
    data = {CONF_NETWORK_KEY_B64: base64.b64encode(b"\x00" * 16).decode()}
    assert entry_has_network_key(data)
    assert entry_needs_network_key_prompt(data) is False


def test_entry_needs_network_key_prompt_with_gen2_device() -> None:
    data = {
        CONF_DEVICES: {"AA:BB:CC:DD:EE:FF": {CONF_DEVICE_GENERATION: GENERATION_GEN2}},
    }
    assert entry_has_gen2_device(data)
    assert entry_needs_network_key_prompt(data) is False


def test_entry_gen2_pairing_locked_with_gen2_and_key() -> None:
    data = {
        CONF_NETWORK_KEY_B64: base64.b64encode(b"\x00" * 16).decode(),
        CONF_DEVICES: {"AA:BB:CC:DD:EE:FF": {CONF_DEVICE_GENERATION: GENERATION_GEN2}},
    }
    assert entry_gen2_pairing_locked(data) is True


def test_entry_needs_network_key_prompt_gen1_only_no_key() -> None:
    data = {
        CONF_DEVICES: {
            "44:67:55:17:D8:4C": {CONF_DEVICE_GENERATION: GENERATION_GEN1},
        },
    }
    assert not entry_has_gen2_device(data)
    assert entry_needs_network_key_prompt(data) is True
