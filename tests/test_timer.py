"""Tests for Gen 2 message merge helper."""

from __future__ import annotations

from pybhyve.gen2_codec import merge_gen2_decoded


def test_merge_gen2_decoded_preserves_device_info() -> None:
    prev = {
        "message": {"deviceInfo": {"numStations": 1, "firmwareVersion": "1.0"}},
        "_framing": {"a": 1},
    }
    new = {
        "message": {
            "deviceStatusInfo": {
                "batteryStatus": {"batteryLevelPercent": 80},
            }
        },
        "_framing": {"b": 2},
    }
    merged = merge_gen2_decoded(prev, new)
    assert merged["message"]["deviceInfo"]["firmwareVersion"] == "1.0"
    assert merged["message"]["deviceStatusInfo"]["batteryStatus"]["batteryLevelPercent"] == 80
    assert merged["_framing"] == {"b": 2}
