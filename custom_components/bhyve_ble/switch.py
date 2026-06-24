from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.switch import SwitchEntity

from .const import BLE_COMMAND_LISTEN_S, BLE_START_CONFIRM_LISTEN_S, DOMAIN
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
    """Start manual watering on one port for the configured run time; ``turn_off`` stops all ports."""

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
            return self.coordinator.gen1_port_is_watering(self._station_id)
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
            await self.coordinator.async_gen1_manual_start(run_sec)
            return
        pt = encode_timer_mode_plaintext(
            "manualMode",
            run_time_sec=run_sec,
            station_id=self._station_id,
        )
        await self.coordinator.async_send_orbit_command(
            pt,
            listen_seconds=BLE_START_CONFIRM_LISTEN_S,
        )
        self.coordinator.schedule_status_refresh_after_run(run_sec)

    async def async_turn_off(self, **kwargs) -> None:
        if self.coordinator.is_gen1:
            await self.coordinator.async_gen1_stop_watering()
            return
        pt = encode_timer_mode_plaintext("offMode")
        await self.coordinator.async_send_orbit_command(
            pt,
            listen_seconds=BLE_COMMAND_LISTEN_S,
        )
