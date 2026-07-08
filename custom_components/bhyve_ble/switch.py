from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.switch import SwitchEntity

from .const import DOMAIN
from .entity import BhyveBleEntity
from .pybhyve.gen2_codec import station_is_actively_watering

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import BhyveBleCoordinator
    from .hub import BhyveBleHub


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    hub: BhyveBleHub = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = []
    for coordinator in hub.coordinators.values():
        await coordinator.async_config_entry_first_refresh()
        n = coordinator.num_stations
        for sid in range(n):
            entities.append(BhyveBleStationManualWateringSwitch(coordinator, sid))
    async_add_entities(entities)


class BhyveBleStationManualWateringSwitch(BhyveBleEntity, SwitchEntity):
    """Start manual watering on one port for the configured run time; turn_off stops all ports."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:sprinkler"

    def __init__(self, coordinator: BhyveBleCoordinator, station_id: int) -> None:
        super().__init__(coordinator)
        self._station_id = station_id
        if station_id == 0:
            self._attr_unique_id = (
                f"{coordinator.entry.entry_id}_{coordinator.address}_manual_watering"
            )
        else:
            self._attr_unique_id = f"{coordinator.entry.entry_id}_{coordinator.address}_station_{station_id}_manual_watering"
        self._attr_name = f"Port {station_id + 1}"

    @property
    def is_on(self) -> bool:
        if self.coordinator.is_gen1:
            return self.coordinator.gen1_station_status(self._station_id).get("state") == "watering"
        lm = (self.coordinator.data or {}).get("last_message")
        active = station_is_actively_watering(
            lm,
            self._station_id,
            num_stations=self.coordinator.num_stations,
        )
        return bool(active)

    async def async_turn_on(self, **kwargs) -> None:
        run_sec = self.coordinator.station_manual_run_seconds(self._station_id)
        if self.coordinator.is_gen1:
            await self.coordinator.async_gen1_start_watering(run_sec)
            return
        await self.coordinator.async_gen2_start_watering(run_sec, station_id=self._station_id)

    async def async_turn_off(self, **kwargs) -> None:
        if self.coordinator.is_gen1:
            await self.coordinator.async_gen1_stop_watering()
            return
        await self.coordinator.async_gen2_stop_watering()
