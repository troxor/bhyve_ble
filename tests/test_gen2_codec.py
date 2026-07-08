from __future__ import annotations

from bhyve_ble.pybhyve.constants import (
    BATTERY_MV_EMPTY,
    BATTERY_MV_FULL,
    estimate_battery_percent_from_mv,
    mv_to_percent_linear,
)
from bhyve_ble.pybhyve.gen2_codec import (
    decode_gen2_ble_plaintext,
    deep_merge_partial_proto_dict,
    encode_get_device_status_info_plaintext,
    format_gen2_battery_line,
    parse_battery_percent_mv_from_decoded,
    resolve_battery_percent_display,
)


def test_gen2_codec_roundtrip_decode_smoke() -> None:
    # We can't assert device semantics without fixtures, but we can ensure the
    # framing is self-consistent and decodes to the expected top-level structure.
    pt = encode_get_device_status_info_plaintext()
    decoded = decode_gen2_ble_plaintext(pt)
    assert "_framing" in decoded
    assert "message" in decoded


def test_to_jsonable_preserves_plain_strings_with_underscores() -> None:
    from bhyve_ble.pybhyve.gen2_codec import _to_jsonable

    payload = {"hwVersion": "GEN2_HT25", "timestampIso8601": "2026-01-01T00:00:00Z"}
    assert _to_jsonable(payload) == payload


def test_parse_battery_nested_preferred_over_legacy() -> None:
    decoded = {
        "message": {
            "deviceStatusInfo": {
                "batteryLevelPercent": 11,
                "batteryLevelMV": 2222,
                "batteryStatus": {"batteryLevelPercent": 84, "batteryLevelMV": 3100},
            }
        }
    }
    pct, mv = parse_battery_percent_mv_from_decoded(decoded)
    assert pct == 84
    assert mv == 3100


def test_parse_battery_legacy_when_no_nested() -> None:
    decoded = {
        "message": {
            "deviceStatusInfo": {
                "batteryLevelPercent": 42,
                "batteryLevelMV": 2600,
            }
        }
    }
    pct, mv = parse_battery_percent_mv_from_decoded(decoded)
    assert pct == 42
    assert mv == 2600


def test_parse_battery_percent_clamped() -> None:
    decoded = {
        "message": {
            "deviceStatusInfo": {
                "batteryStatus": {"batteryLevelPercent": 150},
            }
        }
    }
    pct, mv = parse_battery_percent_mv_from_decoded(decoded)
    assert pct == 100
    assert mv is None


def test_deep_merge_partial_proto_dict_preserves_battery_when_later_update_omits_it() -> None:
    prev_dsi = {
        "deviceStatus": "deviceIdle",
        "batteryStatus": {"batteryLevelPercent": 88, "batteryLevelMV": 3000},
    }
    new_dsi = {
        "deviceStatus": "deviceIdle",
        "wateringStatusSummary": {"sessions": []},
    }
    merged = deep_merge_partial_proto_dict(prev_dsi, new_dsi)
    assert merged["batteryStatus"]["batteryLevelPercent"] == 88
    assert merged["batteryStatus"]["batteryLevelMV"] == 3000
    assert merged["wateringStatusSummary"] == {"sessions": []}


def test_deep_merge_partial_proto_dict_nested_merge() -> None:
    base = {"batteryStatus": {"batteryLevelMV": 2900}}
    upd = {"batteryStatus": {"batteryLevelPercent": 72}}
    merged = deep_merge_partial_proto_dict(base, upd)
    assert merged["batteryStatus"]["batteryLevelMV"] == 2900
    assert merged["batteryStatus"]["batteryLevelPercent"] == 72


def test_parse_battery_none_when_missing() -> None:
    assert parse_battery_percent_mv_from_decoded(None) == (None, None)
    assert parse_battery_percent_mv_from_decoded({}) == (None, None)


def test_mv_to_percent_linear() -> None:
    assert mv_to_percent_linear(3000, BATTERY_MV_EMPTY, BATTERY_MV_FULL) == 100
    assert mv_to_percent_linear(2400, BATTERY_MV_EMPTY, BATTERY_MV_FULL) == 0
    assert mv_to_percent_linear(2833, BATTERY_MV_EMPTY, BATTERY_MV_FULL) == 72
    assert estimate_battery_percent_from_mv(3000) == 100
    assert estimate_battery_percent_from_mv(2833) == 72


def test_resolve_battery_percent_display() -> None:
    assert resolve_battery_percent_display(55, 2833) == (55, "device")
    assert resolve_battery_percent_display(None, 2833) == (72, "estimated_mv")
    assert resolve_battery_percent_display(None, None) == (None, None)


def test_format_gen2_battery_line_estimates_from_mv() -> None:
    line = format_gen2_battery_line(
        {
            "deviceStatusInfo": {
                "batteryStatus": {"batteryLevelMV": 2833},
            }
        }
    )
    assert line == "72% (2833 mV)"


def test_resolve_battery_percent_from_log_fixture() -> None:
    """HT34A-style payload: mV only, no batteryLevelPercent."""
    decoded = {
        "message": {
            "deviceStatusInfo": {
                "batteryStatus": {"batteryLevelMV": 2833},
            }
        }
    }
    pct, mv = parse_battery_percent_mv_from_decoded(decoded)
    assert pct is None
    assert mv == 2833
    assert resolve_battery_percent_display(pct, mv) == (72, "estimated_mv")
