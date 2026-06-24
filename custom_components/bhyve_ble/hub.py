"""One config entry = shared network key; zero or more BLE devices."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import CONF_DEVICES, default_bhyve_device_name, poll_interval_timedelta
from .coordinator import BhyveBleCoordinator

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


class BhyveBleHub:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinators: dict[str, BhyveBleCoordinator] = {}

    async def async_setup(self) -> None:
        devices = self.entry.data.get(CONF_DEVICES) or {}
        for address in devices:
            self.coordinators[address] = BhyveBleCoordinator(
                self.hass,
                self.entry,
                address,
                default_bhyve_device_name(address),
                update_interval=poll_interval_timedelta(self.entry),
            )

    async def async_shutdown(self) -> None:
        for coord in self.coordinators.values():
            await coord.async_shutdown()
        self.coordinators.clear()
