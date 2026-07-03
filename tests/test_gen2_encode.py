from __future__ import annotations

from bhyve_ble.pybhyve.gen2_codec import (
    decode_gen2_ble_plaintext,
    encode_timer_mode_plaintext,
    parse_station_status,
    station_is_actively_watering,
)


def test_encode_manual_mode_includes_station_id() -> None:
    for sid in (0, 1, 2):
        pt = encode_timer_mode_plaintext("manualMode", run_time_sec=120, station_id=sid)
        decoded = decode_gen2_ble_plaintext(pt)
        info = decoded["message"]["timerMode"]["manualModeParams"]["stationInfo"][0]
        assert int(info["stationId"]) == sid
        assert int(info["runTimeSec"]) == 120


def test_multi_port_status_from_timer_mode_station_info() -> None:
    """When legacy wateringStatus is absent, use timerMode station list."""
    decoded = {
        "message": {
            "deviceStatusInfo": {
                "deviceStatus": "wateringInProgress",
                "timerMode": {
                    "mode": "manualMode",
                    "manualModeParams": {
                        "stationInfo": [{"stationId": 1, "runTimeSec": 300}],
                    },
                },
                "wateringStatusSummary": {"sessions": []},
            }
        }
    }
    assert station_is_actively_watering(decoded, 1, num_stations=2) is True
    assert station_is_actively_watering(decoded, 0, num_stations=2) is False
    assert parse_station_status(decoded, 1, num_stations=2)["state"] == "watering"
