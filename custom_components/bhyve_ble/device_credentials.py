"""Resolve per-timer BLE credentials from config entry data."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

from .const import (
    CONF_DEVICE_GENERATION,
    CONF_DEVICE_NETWORK_KEY_B64,
    CONF_DEVICES,
    CONF_DEVICE_ID,
    CONF_NETWORK_KEY_B64,
    GENERATION_GEN1,
    normalize_ble_address,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry


def device_meta(entry: ConfigEntry | Any, address: str) -> dict:
    addr = normalize_ble_address(address)
    return dict((entry.data.get(CONF_DEVICES) or {}).get(addr) or {})


def device_network_key(entry: ConfigEntry | Any, address: str) -> bytes:
    """
    Gen1 timers store a dedicated 16-byte key per device.

    Gen2 timers share the integration entry network_key_b64.
    """
    meta = device_meta(entry, address)
    if meta.get(CONF_DEVICE_GENERATION) == GENERATION_GEN1:
        b64 = meta.get(CONF_DEVICE_NETWORK_KEY_B64)
        if not b64:
            msg = f"gen1 timer {address} is missing device_network_key_b64"
            raise ValueError(msg)
        key = base64.b64decode(b64)
        if len(key) != 16:
            msg = f"gen1 device key for {address} must be 16 bytes"
            raise ValueError(msg)
        return key
    return base64.b64decode(entry.data[CONF_NETWORK_KEY_B64])


def device_id(entry: ConfigEntry | Any, address: str) -> int | None:
    """Gen1 device id from entry metadata (None for gen2)."""
    meta = device_meta(entry, address)
    if meta.get(CONF_DEVICE_GENERATION) != GENERATION_GEN1:
        return None
    raw = meta.get(CONF_DEVICE_ID)
    if raw is None:
        raw = meta.get("mesh_device_id")  # legacy config entry key
    if raw is None:
        return None
    return int(raw)
