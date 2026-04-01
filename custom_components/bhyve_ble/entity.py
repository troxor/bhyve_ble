from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import BhyveBleCoordinator
from .device_info import build_ha_device_info_from_orbit

if TYPE_CHECKING:
    from homeassistant.helpers.device_registry import DeviceInfo


class BhyveBleEntity(CoordinatorEntity[BhyveBleCoordinator]):
    @property
    def device_info(self) -> DeviceInfo:
        return build_ha_device_info_from_orbit(
            address=self.coordinator.address,
            name=self.coordinator.name,
            orbit=self.coordinator.orbit_device_info,
        )
