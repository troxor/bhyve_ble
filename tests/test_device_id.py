"""Display-only device_id helper (gen1 device id vs gen2 MAC suffix)."""

import pytest
from bhyve_ble.pybhyve.constants import format_device_id, normalize_ble_address


def test_normalize_ble_address() -> None:
    assert normalize_ble_address("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"


def test_device_id_gen2_from_mac() -> None:
    assert format_device_id("44:67:55:17:D8:4C", "gen2") == "17d84c"
    assert format_device_id("AA:BB:CC:DD:EE:FF", "gen2") == "ddeeff"


def test_device_id_gen1_from_device_id() -> None:
    assert format_device_id("44:67:55:17:D8:4C", "gen1", 1806) == "1806"


def test_device_id_gen1_requires_device_id() -> None:
    with pytest.raises(ValueError, match="device_id"):
        format_device_id("44:67:55:17:D8:4C", "gen1")
