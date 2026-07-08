from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import GENERATION_GEN1, GENERATION_GEN2
from .coordinator import BhyveBleCoordinator
from .device_info import build_ha_device_info_from_gen2

if TYPE_CHECKING:
    from homeassistant.helpers.device_registry import DeviceInfo


class BhyveBleEntity(CoordinatorEntity[BhyveBleCoordinator]):
    @property
    def device_info(self) -> DeviceInfo:
        return build_ha_device_info_from_gen2(
            address=self.coordinator.address,
            name=self.coordinator.name,
            device_info=self.coordinator.gen2_device_info,
            generation=(GENERATION_GEN1 if self.coordinator.is_gen1 else GENERATION_GEN2),
        )
