from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

DOMAIN = "bhyve_ble"


def normalize_ble_address(address: str) -> str:
    """Normalize BLE MAC for storage and comparison."""
    return address.upper().replace("-", ":")


def default_bhyve_device_name(address: str) -> str:
    """Stable display name: last three octets of the MAC, e.g. ``bhyve_a1b2c3``."""
    mac = normalize_ble_address(address).replace(":", "")
    suffix = mac[-6:] if len(mac) >= 6 else mac
    return f"bhyve_{suffix.lower()}"


# GATT characteristic UUIDs (Orbit B-hyve hose timer over BLE).
NETWORK_CHAR_UUID = "00006c76-fe32-4f58-8b78-98e42b2c047f"
AES_CHAR_UUID = "00006c71-fe32-4f58-8b78-98e42b2c047f"
WRITE_CHAR_UUID = "00006c72-fe32-4f58-8b78-98e42b2c047f"
READ_CHAR_UUID = "00006c73-fe32-4f58-8b78-98e42b2c047f"

CONF_ADDRESS = "address"
CONF_NAME = "name"

# Integration entry: shared key for all timers; devices added later via options flow.
CONF_NETWORK_KEY_B64 = "network_key_b64"
CONF_DEVICES = "devices"  # dict[str, dict] — address -> optional per-device metadata

# Per-device: hardware generation (set once when adding the timer; not user-editable).
CONF_DEVICE_GENERATION = "generation"
GENERATION_GEN1 = "gen1"
GENERATION_GEN2 = "gen2"

# Config flow: optional paste (hex or base64); empty = generate.
CONF_NETWORK_KEY_INPUT = "network_key_input"

# Options: how often to poll each timer for device/status (battery, stations). Stored in hours (float).
CONF_POLL_INTERVAL_HOURS = "poll_interval_hours"
DEFAULT_POLL_INTERVAL_HOURS = 24.0
MIN_POLL_INTERVAL_HOURS = 1 / 60
MAX_POLL_INTERVAL_HOURS = 24 * 14  # 14 days


def poll_interval_timedelta(entry: ConfigEntry) -> timedelta:
    """Coordinator update interval from config entry options (default: once per day)."""
    raw = entry.options.get(CONF_POLL_INTERVAL_HOURS)
    if raw is None:
        return timedelta(hours=DEFAULT_POLL_INTERVAL_HOURS)
    try:
        hours = float(raw)
    except TypeError, ValueError:
        return timedelta(hours=DEFAULT_POLL_INTERVAL_HOURS)
    hours = max(MIN_POLL_INTERVAL_HOURS, min(hours, MAX_POLL_INTERVAL_HOURS))
    return timedelta(seconds=round(hours * 3600))


# Deprecated (v1 single-device entry); kept for migration only.
CONF_IV12_B64 = "iv12_b64"
CONF_ENC_CTR = "enc_ctr"
CONF_DEC_CTR = "dec_ctr"
