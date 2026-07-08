from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from bhyve_ble.const import (
    CONF_DEVICE_GENERATION,
    CONF_DEVICE_NETWORK_KEY_B64,
    CONF_DEVICES,
    CONF_NETWORK_KEY_B64,
    GENERATION_GEN1,
    GENERATION_GEN2,
)
from bhyve_ble.device_credentials import device_network_key

_KEY = b"\x01" * 16


def _entry(*, devices: dict, network_key_b64: str | None = None) -> SimpleNamespace:
    data: dict = {CONF_DEVICES: devices}
    if network_key_b64 is not None:
        data[CONF_NETWORK_KEY_B64] = network_key_b64
    return SimpleNamespace(data=data)


def test_device_network_key_gen2_requires_valid_16_byte_key() -> None:
    entry = _entry(
        devices={"AA:BB:CC:DD:EE:FF": {CONF_DEVICE_GENERATION: GENERATION_GEN2}},
        network_key_b64=base64.b64encode(_KEY).decode("ascii"),
    )
    assert device_network_key(entry, "AA:BB:CC:DD:EE:FF") == _KEY


def test_device_network_key_gen2_rejects_short_key() -> None:
    entry = _entry(
        devices={"AA:BB:CC:DD:EE:FF": {CONF_DEVICE_GENERATION: GENERATION_GEN2}},
        network_key_b64=base64.b64encode(b"\x01" * 8).decode("ascii"),
    )
    with pytest.raises(ValueError, match="16 bytes"):
        device_network_key(entry, "AA:BB:CC:DD:EE:FF")


def test_device_network_key_gen1_rejects_loose_base64() -> None:
    entry = _entry(
        devices={
            "AA:BB:CC:DD:EE:FF": {
                CONF_DEVICE_GENERATION: GENERATION_GEN1,
                CONF_DEVICE_NETWORK_KEY_B64: base64.b64encode(_KEY).decode("ascii")[:-1],
            }
        },
    )
    with pytest.raises(Exception):
        device_network_key(entry, "AA:BB:CC:DD:EE:FF")
