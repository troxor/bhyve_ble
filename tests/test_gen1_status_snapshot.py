from __future__ import annotations

from bhyve_ble.gen1_codec import gen1_status_snapshot_verified


def test_gen1_status_snapshot_verified_device_info() -> None:
    assert gen1_status_snapshot_verified(
        {"device_info": {"kind": "device_info", "firmware_version": 1, "num_stations": 1}}
    )


def test_gen1_status_snapshot_verified_watering_idle() -> None:
    assert gen1_status_snapshot_verified({"watering_idle": {"kind": "watering_idle", "active": False}})


def test_gen1_status_snapshot_verified_empty() -> None:
    assert not gen1_status_snapshot_verified({})
