from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import PERCENTAGE
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .entity import BhyveBleEntity
from .pybhyve.gen2_codec import (
    parse_battery_percent_mv_from_decoded,
    parse_station_status,
    resolve_battery_percent_display,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import BhyveBleCoordinator
    from .hub import BhyveBleHub


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    hub: BhyveBleHub = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []
    for coordinator in hub.coordinators.values():
        n = coordinator.num_stations
        for sid in range(n):
            entities.append(BhyveBleStationStatusSensor(coordinator, sid))
        entities.extend(
            [
                BhyveBleBatterySensor(coordinator),
                BhyveBleBatteryMvSensor(coordinator),
                BhyveBleNumStationsSensor(coordinator),
            ]
        )
    async_add_entities(entities)


class BhyveBleStationStatusSensor(BhyveBleEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:information-outline"

    def __init__(self, coordinator: BhyveBleCoordinator, station_id: int) -> None:
        super().__init__(coordinator)
        self._station_id = station_id
        self._attr_unique_id = (
            f"{coordinator.entry.entry_id}_{coordinator.address}_station_{station_id}_status"
        )
        self._attr_name = f"Port {station_id + 1} status"

    def _status(self) -> dict:
        if self.coordinator.is_gen1:
            return self.coordinator.gen1_station_status(self._station_id)
        last = (self.coordinator.data or {}).get("last_message")
        return parse_station_status(
            last,
            self._station_id,
            num_stations=self.coordinator.num_stations,
        )

    @property
    def native_value(self) -> str:
        return str(self._status().get("state") or "off")

    @property
    def extra_state_attributes(self) -> dict[str, str | int | list[str]]:
        st = self._status()
        attrs: dict[str, str | int | list[str]] = {}
        faults = st.get("faults") or []
        if faults:
            attrs["fault"] = ", ".join(str(f) for f in faults)
            attrs["faults"] = [str(f) for f in faults]
        watering_status = st.get("watering_status")
        if watering_status:
            attrs["watering_status"] = str(watering_status)
        remaining = st.get("remaining_sec")
        if remaining is not None:
            attrs["remaining_sec"] = int(remaining)
        return attrs


class BhyveBleNumStationsSensor(BhyveBleEntity, SensorEntity):
    """Reports deviceInfo.numStations (number of valve ports)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: BhyveBleCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{coordinator.address}_num_stations"
        self._attr_name = "Output Ports"

    @property
    def native_value(self) -> int:
        return self.coordinator.num_stations


class BhyveBleBatterySensor(BhyveBleEntity, SensorEntity):
    """batteryLevelPercent when sent; otherwise estimated from batteryLevelMV."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: BhyveBleCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{coordinator.address}_battery"
        self._attr_name = "Battery"

    def _battery_fields(self) -> tuple[int | None, int | None, str | None]:
        if self.coordinator.is_gen1:
            pct, mv = self.coordinator.gen1_battery_percent_mv()
            display_pct, source = resolve_battery_percent_display(pct, mv)
            return display_pct, mv, source
        pct, mv = parse_battery_percent_mv_from_decoded(self.coordinator.last_message)
        display_pct, source = resolve_battery_percent_display(pct, mv)
        return display_pct, mv, source

    @property
    def native_value(self) -> int | None:
        display_pct, _mv, _source = self._battery_fields()
        return display_pct

    @property
    def extra_state_attributes(self) -> dict[str, int | str]:
        display_pct, mv, source = self._battery_fields()
        attrs: dict[str, int | str] = {}
        if mv is not None:
            attrs["voltage_mv"] = mv
        if source is not None:
            attrs["battery_percent_source"] = source
        if source == "estimated_mv" and display_pct is not None:
            attrs["battery_percent_note"] = (
                "Device sent mV only; percent estimated from voltage (2400-3000 mV)."
            )
        return attrs


class BhyveBleBatteryMvSensor(BhyveBleEntity, SensorEntity):
    """Millivolts from deviceStatusInfo payload."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "mV"

    def __init__(self, coordinator: BhyveBleCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{coordinator.address}_battery_mv"
        self._attr_name = "Battery (mV)"

    @property
    def native_value(self) -> int | None:
        if self.coordinator.is_gen1:
            _pct, mv = self.coordinator.gen1_battery_percent_mv()
            return mv
        _pct, mv = parse_battery_percent_mv_from_decoded(self.coordinator.last_message)
        return mv
