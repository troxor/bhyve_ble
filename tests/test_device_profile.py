"""Tests for generation-specific BLE profiles."""

from bhyve_ble.const import CONF_DEVICE_GENERATION, GENERATION_GEN1, GENERATION_GEN2
from bhyve_ble.device_profile import device_ble_profile, device_ble_profile_from_meta


def test_gen1_profile() -> None:
    p = device_ble_profile(GENERATION_GEN1)
    assert p.tx_delay_ms == 100
    assert p.link_msg_type == 0x10


def test_gen2_profile() -> None:
    p = device_ble_profile(GENERATION_GEN2)
    assert p.tx_delay_ms == 0
    assert p.link_msg_type == 0x11


def test_default_is_gen2() -> None:
    assert device_ble_profile(None).link_msg_type == 0x11
    assert device_ble_profile("unknown").tx_delay_ms == 0
    assert device_ble_profile_from_meta({}).link_msg_type == 0x11
    assert (
        device_ble_profile_from_meta({CONF_DEVICE_GENERATION: GENERATION_GEN1}).tx_delay_ms == 100
    )
