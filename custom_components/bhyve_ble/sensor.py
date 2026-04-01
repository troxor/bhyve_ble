from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .entity import BhyveBleEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import BhyveBleCoordinator
    from .hub import BhyveBleHub


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    hub: BhyveBleHub = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []
    for coordinator in hub.coordinators.values():
        entities.extend(
            [
                BhyveBleLastOneofSensor(coordinator),
                BhyveBleBatteryMvSensor(coordinator),
                BhyveBleNumStationsSensor(coordinator),
            ]
        )
    async_add_entities(entities)


class BhyveBleLastOneofSensor(BhyveBleEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: BhyveBleCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{coordinator.address}_last_oneof"
        self._attr_name = "Last message type"

    @property
    def native_value(self) -> str:
        msg = (self.coordinator.data or {}).get("last_message") or {}
        framing = msg.get("_framing") or {}
        return framing.get("oneof") or "unknown"


class BhyveBleNumStationsSensor(BhyveBleEntity, SensorEntity):
    """Reports ``deviceInfo.numStations`` (number of valve ports)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: BhyveBleCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{coordinator.address}_num_stations"
        self._attr_name = "Output Ports"

    @property
    def native_value(self) -> int:
        return self.coordinator.num_stations


class BhyveBleBatteryMvSensor(BhyveBleEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "mV"

    def __init__(self, coordinator: BhyveBleCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{coordinator.address}_battery_mv"
        self._attr_name = "Battery (mV)"

    @property
    def native_value(self) -> int | None:
        msg = (self.coordinator.data or {}).get("last_message") or {}
        m = msg.get("message") or {}
        dsi = m.get("deviceStatusInfo") or {}
        bat = dsi.get("batteryStatus") or {}
        mv = bat.get("batteryLevelMV")
        try:
            return int(mv) if mv is not None else None
        except TypeError, ValueError:
            return None
