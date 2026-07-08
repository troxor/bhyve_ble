from __future__ import annotations

from bhyve_ble.pybhyve.gen2_codec import normalize_num_stations, parse_num_stations_from_decoded


def test_normalize_num_stations_known_counts() -> None:
    assert normalize_num_stations(1) == 1
    assert normalize_num_stations(2) == 2
    assert normalize_num_stations(4) == 4


def test_normalize_num_stations_clamps_unknown() -> None:
    assert normalize_num_stations(None) == 1
    assert normalize_num_stations(0) == 1
    assert normalize_num_stations(3) == 2
    assert normalize_num_stations(64) == 4


def test_parse_num_stations_from_decoded_normalizes() -> None:
    decoded = {"message": {"deviceInfo": {"numStations": 4}}}
    assert parse_num_stations_from_decoded(decoded) == 4
