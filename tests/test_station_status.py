from __future__ import annotations

from bhyve_ble.pybhyve.gen2_codec import (
    deep_merge_device_status_info,
    parse_station_faults,
    parse_station_status,
    station_is_actively_watering,
)


def test_parse_station_status_defaults_off() -> None:
    assert parse_station_status(None, 0)["state"] == "unknown"
    assert parse_station_status({}, 0)["state"] == "off"


def test_parse_station_status_watering_session() -> None:
    decoded = {
        "message": {
            "deviceStatusInfo": {
                "wateringStatusSummary": {
                    "sessions": [
                        {
                            "currentStationId": 1,
                            "status": "wateringInProgress",
                            "currentTimeRemainingSec": 120,
                        }
                    ]
                }
            }
        }
    }
    st = parse_station_status(decoded, 1)
    assert st["state"] == "watering"
    assert st["remaining_sec"] == 120
    assert st["watering_status"] == "wateringInProgress"
    assert parse_station_status(decoded, 0)["state"] == "off"


def test_parse_station_status_delay() -> None:
    decoded = {
        "message": {
            "deviceStatusInfo": {
                "wateringStatusSummary": {
                    "sessions": [
                        {
                            "currentStationId": 0,
                            "status": "stationDelay",
                            "currentTimeRemainingSec": 5,
                        }
                    ]
                }
            }
        }
    }
    assert parse_station_status(decoded, 0)["state"] == "delay"
    assert station_is_actively_watering(decoded, 0) is False


def test_parse_station_faults_from_station_list() -> None:
    decoded = {
        "message": {
            "deviceStatusInfo": {
                "faultStatus": {
                    "stationFaults": [
                        {"stationId": 2, "highFlow": {"flowGpm": 2.5}},
                        {"stationId": 0, "noFlow": {}},
                    ],
                }
            }
        }
    }
    assert parse_station_faults(decoded, 2) == ["highFlow"]
    assert parse_station_faults(decoded, 0) == ["noFlow"]


def test_parse_station_status_fault_when_idle() -> None:
    decoded = {
        "message": {
            "deviceStatusInfo": {
                "faultStatus": {
                    "stationFaults": [{"stationId": 0, "noFlow": {}}],
                }
            }
        }
    }
    st = parse_station_status(decoded, 0)
    assert st["state"] == "fault"
    assert st["faults"] == ["noFlow"]


def test_deep_merge_device_status_info_drops_stale_watering_status() -> None:
    prev_dsi = {
        "wateringStatus": {
            "status": "wateringInProgress",
            "currentStationId": 0,
        },
        "batteryStatus": {"batteryLevelPercent": 90},
    }
    new_dsi = {
        "deviceStatus": "deviceIdle",
        "wateringStatusSummary": {"sessions": []},
    }
    merged = deep_merge_device_status_info(prev_dsi, new_dsi)
    assert "wateringStatus" not in merged
    assert merged["batteryStatus"]["batteryLevelPercent"] == 90
    assert merged["wateringStatusSummary"] == {"sessions": []}


def test_deep_merge_device_status_info_keeps_watering_when_summary_empty() -> None:
    prev_dsi = {
        "deviceStatus": "wateringInProgress",
        "wateringStatus": {
            "status": "wateringInProgress",
            "currentStationId": 0,
            "currentTimeRemainingSec": 537,
        },
    }
    new_dsi = {"wateringStatusSummary": {"sessions": []}}
    merged = deep_merge_device_status_info(prev_dsi, new_dsi)
    assert merged["wateringStatus"]["currentTimeRemainingSec"] == 537


def test_parse_station_status_idle_summary_ignores_stale_legacy_watering() -> None:
    """After merge, idle summary clears stale nested legacy wateringStatus."""
    prev_dsi = {
        "wateringStatus": {
            "status": "wateringInProgress",
            "currentStationId": 0,
            "currentTimeRemainingSec": 60,
        },
        "batteryStatus": {"batteryLevelPercent": 90},
    }
    merged_dsi = deep_merge_device_status_info(
        prev_dsi,
        {"deviceStatus": "deviceIdle", "wateringStatusSummary": {"sessions": []}},
    )
    decoded = {"message": {"deviceStatusInfo": merged_dsi}}
    st = parse_station_status(decoded, 0)
    assert st["state"] == "off"
    assert station_is_actively_watering(decoded, 0) is False


def test_parse_station_status_top_level_watering_oneof() -> None:
    """Gen2 NOTIFY may use a top-level ``wateringStatus`` oneof branch."""
    decoded = {
        "message": {
            "deviceStatusInfo": {"wateringStatusSummary": {"sessions": []}},
            "wateringStatus": {
                "status": "wateringInProgress",
                "currentStationId": 0,
                "currentTimeRemainingSec": 45,
            },
        }
    }
    st = parse_station_status(decoded, 0)
    assert st["state"] == "watering"
    assert st["remaining_sec"] == 45
    assert station_is_actively_watering(decoded, 0) is True


def test_parse_station_status_lab_shape_device_status_watering() -> None:
    """HT25G2 status response: deviceStatus + legacy watering, empty summary."""
    decoded = {
        "message": {
            "deviceStatusInfo": {
                "deviceStatus": "wateringInProgress",
                "timerMode": {
                    "mode": "manualMode",
                    "manualModeParams": {
                        "stationInfo": [{"stationId": 0, "runTimeSec": 600}],
                    },
                },
                "wateringStatusSummary": {"sessions": []},
                "wateringStatus": {
                    "status": "wateringInProgress",
                    "currentStationId": 0,
                    "currentTimeRemainingSec": 537,
                },
            }
        }
    }
    st = parse_station_status(decoded, 0)
    assert st["state"] == "watering"
    assert st["remaining_sec"] == 537
    assert station_is_actively_watering(decoded, 0) is True


def test_parse_station_status_device_status_only_when_legacy_dropped() -> None:
    decoded = {
        "message": {
            "deviceStatusInfo": {
                "deviceStatus": "wateringInProgress",
                "wateringStatusSummary": {"sessions": []},
                "timerMode": {
                    "mode": "manualMode",
                    "manualModeParams": {
                        "stationInfo": [{"stationId": 0, "runTimeSec": 600}],
                    },
                },
            }
        }
    }
    st = parse_station_status(decoded, 0)
    assert st["state"] == "watering"
    assert station_is_actively_watering(decoded, 0) is True


def test_parse_battery_top_level_oneof() -> None:
    from bhyve_ble.pybhyve.gen2_codec import parse_battery_percent_mv_from_decoded

    decoded = {
        "message": {
            "batteryStatus": {"batteryLevelMV": 2910, "batteryLevelPercent": 76},
        }
    }
    pct, mv = parse_battery_percent_mv_from_decoded(decoded)
    assert pct == 76
    assert mv == 2910
