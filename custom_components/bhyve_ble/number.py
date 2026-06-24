from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTime

from .const import DOMAIN
from .entity import BhyveBleEntity
from .orbit_codec import MANUAL_WATER_RUN_SEC_MAX, MANUAL_WATER_RUN_SEC_MIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import BhyveBleCoordinator
    from .hub import BhyveBleHub


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    hub: BhyveBleHub = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = []
    for coordinator in hub.coordinators.values():
        n = coordinator.num_stations
        for sid in range(n):
            entities.append(BhyveBleStationRunTimeNumber(coordinator, sid))
    async_add_entities(entities)


class BhyveBleStationRunTimeNumber(BhyveBleEntity, NumberEntity):
    """Manual watering duration (seconds) used when the port switch is turned on."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_native_min_value = float(MANUAL_WATER_RUN_SEC_MIN)
    _attr_native_max_value = float(MANUAL_WATER_RUN_SEC_MAX)
    _attr_native_step = 1.0
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator: BhyveBleCoordinator, station_id: int) -> None:
        super().__init__(coordinator)
        self._station_id = station_id
        self._attr_unique_id = (
            f"{coordinator.entry.entry_id}_{coordinator.address}_station_{station_id}_run_time"
        )
        self._attr_name = f"Port {station_id + 1} run time"
        self._attr_native_value = float(coordinator.station_manual_run_seconds(station_id))

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.native_value is not None:
            self.coordinator.set_station_manual_run_seconds(
                self._station_id, int(self.native_value)
            )

    async def async_set_native_value(self, value: float) -> None:
        seconds = int(value)
        self._attr_native_value = float(seconds)
        self.coordinator.set_station_manual_run_seconds(self._station_id, seconds)
        self.async_write_ha_state()
