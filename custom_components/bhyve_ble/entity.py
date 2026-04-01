from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .device_info import build_ha_device_info_from_orbit
from .coordinator import BhyveBleCoordinator


class BhyveBleEntity(CoordinatorEntity[BhyveBleCoordinator]):
    @property
    def device_info(self) -> DeviceInfo:
        return build_ha_device_info_from_orbit(
            address=self.coordinator.address,
            name=self.coordinator.name,
            orbit=self.coordinator.orbit_device_info,
        )

