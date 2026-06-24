"""Discover gen1 mesh / BLE device id from advertisements (pairing mode)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .const import normalize_ble_address

if TYPE_CHECKING:
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Orbit gen1 timers use company id 0xFFFF in pairing-mode adverts.
GEN1_MESH_MANUFACTURER_ID = 0xFFFF

# Observed prefix when the timer is advertising for pairing; mesh id follows at offset 2.
GEN1_PAIRING_ADVERT_PREFIX = b"\x06\x00"

GEN1_MESH_DISCOVERY_TIMEOUT_S = 8


def mesh_id_from_manufacturer_blob(blob: bytes) -> int | None:
    """
    Parse mesh device id from Orbit 0xFFFF manufacturer payload.

    Pairing-ready adverts: ``06 00`` + LE uint16 mesh id (e.g. ``06 00 08 80`` → 32776).
    Some units may omit the prefix and advertise only the 2-byte mesh id.
    A lone ``06 00`` (timer visible but not pairing-ready) must not decode as mesh id 6.
    """
    if len(blob) < 2:
        return None
    if len(blob) >= 4 and blob[0:2] == GEN1_PAIRING_ADVERT_PREFIX:
        mesh_id = int.from_bytes(blob[2:4], "little")
    else:
        mesh_id = int.from_bytes(blob[0:2], "little")
    if mesh_id == 0:
        return None
    if blob[0:2] == GEN1_PAIRING_ADVERT_PREFIX and len(blob) < 4:
        return None
    return mesh_id


def mesh_id_from_manufacturer_data(manufacturer_data: dict[int, bytes]) -> int | None:
    """Parse mesh device id from BLE manufacturer data (company 0xFFFF only)."""
    blob = manufacturer_data.get(GEN1_MESH_MANUFACTURER_ID)
    if blob is None:
        return None
    return mesh_id_from_manufacturer_blob(blob)


def _mesh_id_from_service_info(info: BluetoothServiceInfoBleak) -> int | None:
    return mesh_id_from_manufacturer_data(dict(info.manufacturer_data or {}))


def _mesh_id_from_cache(hass: HomeAssistant, target: str) -> int | None:
    from homeassistant.components.bluetooth import async_discovered_service_info

    for info in async_discovered_service_info(hass, connectable=True):
        if normalize_ble_address(info.address) != target:
            continue
        mesh_id = _mesh_id_from_service_info(info)
        if mesh_id is not None:
            return mesh_id
    return None


async def async_discover_gen1_mesh_device_id(hass: HomeAssistant, address: str) -> int | None:
    """
    Best-effort mesh id while the timer is in pairing mode.

    Listens up to ``GEN1_MESH_DISCOVERY_TIMEOUT_S`` for an advertisement from
    ``address``, then falls back to Home Assistant's Bluetooth cache.
    """
    from homeassistant.components.bluetooth import (
        BluetoothScanningMode,
        async_process_advertisements,
    )

    target = normalize_ble_address(address)

    def _has_mesh_id(info: BluetoothServiceInfoBleak) -> bool:
        return _mesh_id_from_service_info(info) is not None

    try:
        info = await async_process_advertisements(
            hass,
            _has_mesh_id,
            {"address": target},
            BluetoothScanningMode.ACTIVE,
            GEN1_MESH_DISCOVERY_TIMEOUT_S,
        )
    except TimeoutError:
        _LOGGER.debug(
            "No gen1 mesh id in advertisements from %s within %ss; trying cache",
            target,
            GEN1_MESH_DISCOVERY_TIMEOUT_S,
        )
    else:
        mesh_id = _mesh_id_from_service_info(info)
        if mesh_id is not None:
            raw = (info.manufacturer_data or {}).get(GEN1_MESH_MANUFACTURER_ID)
            _LOGGER.debug(
                "Discovered gen1 mesh id %s for %s (0xFFFF=%s)",
                mesh_id,
                target,
                raw.hex() if raw else "",
            )
            return mesh_id

    return _mesh_id_from_cache(hass, target)
