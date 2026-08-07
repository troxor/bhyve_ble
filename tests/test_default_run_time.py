"""Tests for integration-wide default manual watering duration."""

from __future__ import annotations

from types import SimpleNamespace

from bhyve_ble.const import (
    CONF_DEFAULT_MANUAL_WATER_RUN_SEC,
    DEFAULT_MANUAL_WATER_RUN_SEC,
    MANUAL_WATER_RUN_SEC_MAX,
    MANUAL_WATER_RUN_SEC_MIN,
    default_manual_water_run_seconds,
)


def test_default_manual_water_run_seconds_fallback() -> None:
    entry = SimpleNamespace(options={})
    assert default_manual_water_run_seconds(entry) == DEFAULT_MANUAL_WATER_RUN_SEC


def test_default_manual_water_run_seconds_from_options() -> None:
    entry = SimpleNamespace(options={CONF_DEFAULT_MANUAL_WATER_RUN_SEC: 120})
    assert default_manual_water_run_seconds(entry) == 120


def test_default_manual_water_run_seconds_clamped() -> None:
    low = SimpleNamespace(options={CONF_DEFAULT_MANUAL_WATER_RUN_SEC: 1})
    high = SimpleNamespace(options={CONF_DEFAULT_MANUAL_WATER_RUN_SEC: 99_999})
    assert default_manual_water_run_seconds(low) == MANUAL_WATER_RUN_SEC_MIN
    assert default_manual_water_run_seconds(high) == MANUAL_WATER_RUN_SEC_MAX


def test_default_manual_water_run_seconds_invalid() -> None:
    entry = SimpleNamespace(options={CONF_DEFAULT_MANUAL_WATER_RUN_SEC: "nope"})
    assert default_manual_water_run_seconds(entry) == DEFAULT_MANUAL_WATER_RUN_SEC
