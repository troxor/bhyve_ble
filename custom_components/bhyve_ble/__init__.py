from __future__ import annotations

from typing import TYPE_CHECKING

from .const import (
    CONF_ADDRESS,
    CONF_DEC_CTR,
    CONF_DEVICES,
    CONF_ENC_CTR,
    CONF_IV12_B64,
    CONF_NAME,
    DOMAIN,
    normalize_ble_address,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceEntry

PLATFORMS: list[str] = ["sensor", "switch"]


def _import_orbit_codec() -> None:
    """Load orbit_codec in a worker thread; protobuf triggers C-extension imports."""
    from . import orbit_codec  # noqa: F401


def _sync_default_device_names_to_registry_impl(hass: HomeAssistant, entry: ConfigEntry) -> None:
    from homeassistant.helpers import device_registry as dr

    hub = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if hub is None:
        return
    reg = dr.async_get(hass)
    for address, coordinator in hub.coordinators.items():
        display = coordinator.name.strip() if coordinator.name else ""
        if not display:
            continue
        device = reg.async_get_device(identifiers={(DOMAIN, address)})
        if device is None:
            continue
        if device.name_by_user:
            continue
        if device.name != display:
            reg.async_update_device(device.id, name=display)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.version == 1:
        data = dict(entry.data)
        addr = data.pop(CONF_ADDRESS, None)
        data.pop(CONF_NAME, None)
        for k in (CONF_IV12_B64, CONF_ENC_CTR, CONF_DEC_CTR):
            data.pop(k, None)
        if addr:
            data[CONF_DEVICES] = {addr: {}}
        else:
            data.setdefault(CONF_DEVICES, {})
        hass.config_entries.async_update_entry(entry, data=data, version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from homeassistant.core import callback

    await hass.async_add_executor_job(_import_orbit_codec)

    from .hub import BhyveBleHub

    hub = BhyveBleHub(hass, entry)
    await hub.async_setup()

    # Refresh coordinators for any configured devices.
    for coordinator in hub.coordinators.values():
        await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = hub
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    callback(_sync_default_device_names_to_registry_impl)(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hub = hass.data[DOMAIN].pop(entry.entry_id, None)
        if hub:
            await hub.async_shutdown()
    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    address: str | None = None
    for domain, identifier in device_entry.identifiers:
        if domain == DOMAIN:
            address = normalize_ble_address(str(identifier))
            break
    if address is None:
        return False

    devices = dict(config_entry.data.get(CONF_DEVICES) or {})
    if address not in devices:
        return False

    devices.pop(address)
    hass.config_entries.async_update_entry(
        config_entry,
        data={**config_entry.data, CONF_DEVICES: devices},
    )
    await hass.config_entries.async_reload(config_entry.entry_id)
    return True
