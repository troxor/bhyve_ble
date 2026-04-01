from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.switch import SwitchEntity

from .const import DOMAIN
from .entity import BhyveBleEntity
from .orbit_codec import encode_timer_mode_plaintext, station_is_actively_watering

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
    """Manual watering for one station (port); ``turn_off`` stops all stations (off mode)."""

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
    def is_on(self) -> bool | None:
        lm = (self.coordinator.data or {}).get("last_message")
        return station_is_actively_watering(
            lm,
            self._station_id,
            num_stations=self.coordinator.num_stations,
        )

    async def async_turn_on(self, **kwargs) -> None:
        pt = encode_timer_mode_plaintext(
            "manualMode",
            run_time_sec=600,
            station_id=self._station_id,
        )
        await self.coordinator.async_send_orbit_plaintext(pt)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        pt = encode_timer_mode_plaintext("offMode")
        await self.coordinator.async_send_orbit_plaintext(pt)
        await self.coordinator.async_request_refresh()
