"""BLE session timing defaults (power-conscious connect/disconnect)."""

from custom_components.bhyve_ble.const import (
    BLE_COMMAND_LISTEN_S,
    BLE_START_CONFIRM_LISTEN_S,
    BLE_STATUS_LISTEN_S,
    DEFAULT_POLL_INTERVAL_HOURS,
)


def test_default_poll_is_daily() -> None:
    assert DEFAULT_POLL_INTERVAL_HOURS == 24.0


def test_listen_windows_are_short() -> None:
    """Sessions should not hold the link open for long idle listens."""
    assert 0 < BLE_STATUS_LISTEN_S <= 5
    assert 0 < BLE_COMMAND_LISTEN_S <= 5
    assert BLE_START_CONFIRM_LISTEN_S >= BLE_COMMAND_LISTEN_S
