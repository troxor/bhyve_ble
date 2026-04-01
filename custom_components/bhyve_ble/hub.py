"""One config entry = shared network key; zero or more BLE devices."""

from __future__ import annotations

import base64
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_DEVICES, CONF_NETWORK_KEY_B64, DOMAIN, default_bhyve_device_name
from .coordinator import BhyveBleCoordinator

_LOGGER = logging.getLogger(__name__)


class BhyveBleHub:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinators: dict[str, BhyveBleCoordinator] = {}

    @property
    def network_key(self) -> bytes:
        return base64.b64decode(self.entry.data[CONF_NETWORK_KEY_B64])

    async def async_setup(self) -> None:
        devices = self.entry.data.get(CONF_DEVICES) or {}
        for address, _dev in devices.items():
            self.coordinators[address] = BhyveBleCoordinator(
                self.hass,
                self.entry,
                address,
                default_bhyve_device_name(address),
            )

    async def async_shutdown(self) -> None:
        for coord in self.coordinators.values():
            await coord.async_shutdown()
        self.coordinators.clear()
