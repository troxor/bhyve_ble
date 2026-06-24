from __future__ import annotations

from bhyve_ble.gen1_codec import (
    gen1_device_info_for_registry,
    gen1_is_watering,
    parse_gen1_battery_percent_mv,
    parse_gen1_station_status,
)


def test_gen1_is_watering_active_only() -> None:
    assert not gen1_is_watering(None)
    assert not gen1_is_watering({})
    assert gen1_is_watering({"watering_status": {"kind": "watering_status", "active": True}})
    assert not gen1_is_watering({"watering_status": {"kind": "watering_status", "active": False}})
    assert not gen1_is_watering({"watering_idle": {"kind": "watering_idle", "active": False}})


def test_parse_gen1_station_status() -> None:
    snap = {
        "device_info": {"kind": "device_info", "firmware_version": 3, "num_stations": 1},
        "watering_idle": {"kind": "watering_idle", "active": False},
    }
    assert parse_gen1_station_status(snap, 0)["state"] == "off"
    assert parse_gen1_station_status(snap, 1)["state"] == "off"
    assert parse_gen1_station_status(None, 0)["state"] == "unknown"

    watering = {
        "watering_status": {
            "kind": "watering_status",
            "active": True,
            "remaining_sec": 120,
            "total_sec": 600,
        }
    }
    st = parse_gen1_station_status(watering, 0)
    assert st["state"] == "watering"
    assert st["remaining_sec"] == 120


def test_parse_gen1_battery() -> None:
    snap = {
        "battery": {
            "kind": "battery",
            "battery_mv": 2800,
            "battery_percent": 66,
        }
    }
    pct, mv = parse_gen1_battery_percent_mv(snap)
    assert pct == 66
    assert mv == 2800


def test_gen1_device_info_for_registry() -> None:
    mapped = gen1_device_info_for_registry(
        {
            "device_info": {
                "kind": "device_info",
                "model": "HT25",
                "firmware_version": 5,
                "num_stations": 1,
            }
        }
    )
    assert mapped is not None
    assert mapped["hwVersion"] == "HT25"
    assert mapped["fwVersion"] == "5"
    assert mapped["numStations"] == 1
