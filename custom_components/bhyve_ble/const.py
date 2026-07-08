from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

DOMAIN = "bhyve_ble"

from .pybhyve import constants as _ble_constants

normalize_ble_address = _ble_constants.normalize_ble_address
format_device_id = _ble_constants.format_device_id
VALID_TIMER_PORT_COUNTS = _ble_constants.VALID_TIMER_PORT_COUNTS
MAX_TIMER_PORTS = _ble_constants.MAX_TIMER_PORTS
MAX_TIMER_STATION_ID = _ble_constants.MAX_TIMER_STATION_ID


def default_bhyve_device_name(address: str) -> str:
    """Stable display name: last three octets of the MAC, e.g. bhyve_a1b2c3."""
    return f"bhyve_{format_device_id(address, 'gen2')}"


CONF_ADDRESS = "address"
CONF_NAME = "name"

# Integration entry: shared key for all timers; devices added later via options flow.
CONF_NETWORK_KEY_B64 = "network_key_b64"
CONF_DEVICES = "devices"  # dict[str, dict] — address -> optional per-device metadata

# Per-device: hardware generation (set once when adding the timer; not user-editable).
CONF_DEVICE_GENERATION = "generation"
GENERATION_GEN1 = "gen1"
GENERATION_GEN2 = "gen2"

# B-hyve hose timers ship with 1, 2, or 4 valve ports (0-based station ids 0..3).
# Values re-exported from pybhyve.constants above.

# Gen1 only: per-timer device id and dedicated network key (gen2 shares entry-level key).
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_NETWORK_KEY_B64 = "device_network_key_b64"

# Config flow: optional paste (hex or base64); empty = generate.
CONF_NETWORK_KEY_INPUT = "network_key_input"

# Gen1 credentials step: when true, run full pairing/onboard; when false, verify existing creds.
CONF_GEN1_RUN_PAIRING = "gen1_run_pairing"

# Gen2 credentials step: when true, provision + verify; when false, verify only.
CONF_GEN2_RUN_PAIRING = "gen2_run_pairing"

# Options: how often to poll each timer for device/status (battery, stations). Stored in hours (float).
CONF_POLL_INTERVAL_HOURS = "poll_interval_hours"
DEFAULT_POLL_INTERVAL_HOURS = 24.0
MIN_POLL_INTERVAL_HOURS = 1 / 60
MAX_POLL_INTERVAL_HOURS = 24 * 14  # 14 days

# Default manual run when turning a station on (seconds); per-station Number entity overrides.
DEFAULT_MANUAL_WATER_RUN_SEC = 600


def poll_interval_timedelta(entry: ConfigEntry) -> timedelta:
    """Coordinator update interval from config entry options (default: once per day)."""
    raw = entry.options.get(CONF_POLL_INTERVAL_HOURS)
    if raw is None:
        return timedelta(hours=DEFAULT_POLL_INTERVAL_HOURS)
    try:
        hours = float(raw)
    except (TypeError, ValueError):  # fmt: skip
        return timedelta(hours=DEFAULT_POLL_INTERVAL_HOURS)
    hours = max(MIN_POLL_INTERVAL_HOURS, min(hours, MAX_POLL_INTERVAL_HOURS))
    return timedelta(seconds=round(hours * 3600))


# Deprecated (v1 single-device entry); kept for migration only.
CONF_IV12_B64 = "iv12_b64"
CONF_ENC_CTR = "enc_ctr"
CONF_DEC_CTR = "dec_ctr"
