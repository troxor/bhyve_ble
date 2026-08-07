from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_DEVICE_GENERATION,
    DOMAIN,
    GENERATION_GEN1,
    default_manual_water_run_seconds,
)
from .device_credentials import device_id, device_meta, device_network_key
from .device_profile import DeviceBleProfile, device_ble_profile_from_meta
from .gen1_runtime import (
    Gen1BleSessionParams,
    Gen1RuntimeError,
    Gen1SessionResult,
    async_gen1_manual_start,
    async_gen1_stop_watering,
    async_read_gen1_status,
)
from .gen2_runtime import (
    Gen2BleSessionParams,
    Gen2RuntimeError,
    Gen2SessionResult,
    async_gen2_manual_start,
    async_gen2_stop_watering,
    async_read_gen2_status,
)
from .logging import log_ble_merged
from .pybhyve.gen1_codec import (
    gen1_device_info_for_registry,
    parse_gen1_battery_percent_mv,
    parse_gen1_station_status,
)
from .pybhyve.gen2_codec import (
    MANUAL_WATER_RUN_SEC_MAX,
    MANUAL_WATER_RUN_SEC_MIN,
    normalize_num_stations,
    parse_num_stations_from_decoded,
)

if TYPE_CHECKING:
    from datetime import timedelta

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class BhyveBleCoordinator(DataUpdateCoordinator[dict]):
    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        address: str,
        name: str,
        *,
        update_interval: timedelta,
    ) -> None:
        self.entry = entry
        self.address = address
        self.name = name
        meta = device_meta(entry, address)
        profile = device_ble_profile_from_meta(meta)
        self._profile: DeviceBleProfile = profile
        self._generation = meta.get(CONF_DEVICE_GENERATION, profile.generation)
        self._network_key16 = device_network_key(entry, address)
        self._device_id = device_id(entry, address)
        self._last_message: dict | None = None
        self._device_info: dict | None = None
        self._gen1_snapshot: dict = {}
        self._session_lock = asyncio.Lock()
        self._station_run_sec: dict[int, int] = {}
        self._defer_refresh_unsubs: list[CALLBACK_TYPE] = []

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{self.address}",
            update_interval=update_interval,
        )

    def station_manual_run_seconds(self, station_id: int) -> int:
        """Manual watering duration for station_id (seconds)."""
        raw = self._station_run_sec.get(station_id, default_manual_water_run_seconds(self.entry))
        return max(MANUAL_WATER_RUN_SEC_MIN, min(int(raw), MANUAL_WATER_RUN_SEC_MAX))

    def set_station_manual_run_seconds(self, station_id: int, seconds: int) -> None:
        self._station_run_sec[station_id] = max(
            MANUAL_WATER_RUN_SEC_MIN,
            min(int(seconds), MANUAL_WATER_RUN_SEC_MAX),
        )

    @property
    def is_gen1(self) -> bool:
        return self._generation == GENERATION_GEN1

    def _gen2_session_params(self) -> Gen2BleSessionParams:
        return Gen2BleSessionParams(
            address=self.address,
            network_key_16=self._network_key16,
            ble_profile=self._profile,
        )

    def _apply_gen2_result(self, result: Gen2SessionResult) -> None:
        if result.last_message is not None:
            self._last_message = result.last_message
        self._sync_device_info_from_last_message()

    @property
    def gen2_device_info(self) -> dict | None:
        """Latest decoded deviceInfo submessage from the timer, if any."""
        return self._device_info

    @property
    def last_message(self) -> dict | None:
        """Latest merged gen2 decode (``message`` + ``_framing``), or None."""
        return self._last_message

    @property
    def num_stations(self) -> int:
        """Number of valve ports from deviceInfo.numStations (default 1 until known)."""
        if self.is_gen1:
            return 1
        if self._device_info and self._device_info.get("numStations") is not None:
            try:
                return normalize_num_stations(int(self._device_info["numStations"]))
            except (TypeError, ValueError):  # fmt: skip
                pass
        n = parse_num_stations_from_decoded(self._last_message)
        if n is not None:
            return n
        return 1

    def _coordinator_payload(self) -> dict:
        return {
            "address": self.address,
            "name": self.name,
            "last_message": self._last_message,
            "is_gen1": self.is_gen1,
            "num_stations": self.num_stations,
            "gen1_snapshot": dict(self._gen1_snapshot) if self.is_gen1 else None,
        }

    def _gen1_session_params(self) -> Gen1BleSessionParams:
        if self._device_id is None:
            msg = f"gen1 timer {self.address} is missing its device_id"
            raise UpdateFailed(msg)
        return Gen1BleSessionParams(
            address=self.address,
            network_key_16=self._network_key16,
            device_id=int(self._device_id),
            ble_profile=self._profile,
        )

    def gen1_station_status(self, station_id: int) -> dict:
        """Per-port status dict (same keys as gen2 parse_station_status)."""
        return parse_gen1_station_status(self._gen1_snapshot or None, station_id)

    def gen1_battery_percent_mv(self) -> tuple[int | None, int | None]:
        """(percent, millivolts) from the latest gen1 status snapshot."""
        return parse_gen1_battery_percent_mv(self._gen1_snapshot or None)

    def _apply_gen1_result(self, result: Gen1SessionResult) -> None:
        self._gen1_snapshot = dict(result.snapshot or {})
        di = gen1_device_info_for_registry(self._gen1_snapshot or None)
        if di:
            self._device_info = di

    def _cancel_deferred_refresh_callbacks(self) -> None:
        for unsub in self._defer_refresh_unsubs:
            unsub()
        self._defer_refresh_unsubs.clear()

    def _schedule_deferred_status_refresh(self, delay_sec: float) -> None:
        """Poll status after manual watering (coordinator interval is too slow)."""

        @callback
        def _refresh(_now) -> None:
            self.hass.async_create_task(self.async_request_refresh())

        self._defer_refresh_unsubs.append(
            async_call_later(self.hass, max(1.0, float(delay_sec)), _refresh)
        )

    def schedule_status_refresh_after_run(self, duration_sec: int) -> None:
        """Schedule mid-run and post-run coordinator polls."""
        self._cancel_deferred_refresh_callbacks()
        duration = max(15, min(int(duration_sec), 4 * 3600))
        self._schedule_deferred_status_refresh(min(30.0, duration / 2))
        self._schedule_deferred_status_refresh(float(duration) + 15.0)

    def _sync_device_info_from_last_message(self) -> None:
        if not self._last_message:
            return
        di = (self._last_message.get("message") or {}).get("deviceInfo")
        if di:
            self._device_info = di

    async def async_shutdown(self) -> None:
        self._cancel_deferred_refresh_callbacks()

    async def async_gen2_start_watering(self, duration_sec: int, *, station_id: int = 0) -> None:
        """Start manual watering on one gen2 port, then refresh state."""
        async with self._session_lock:
            try:
                result = await async_gen2_manual_start(
                    self.hass,
                    self._gen2_session_params(),
                    int(duration_sec),
                    station_id=station_id,
                )
            except Gen2RuntimeError as e:
                raise UpdateFailed(str(e)) from e
        self._apply_gen2_result(result)
        self.async_set_updated_data(self._coordinator_payload())
        self.schedule_status_refresh_after_run(int(duration_sec))

    async def async_gen2_stop_watering(self) -> None:
        """Stop watering on the gen2 timer, then refresh state."""
        async with self._session_lock:
            try:
                result = await async_gen2_stop_watering(self.hass, self._gen2_session_params())
            except Gen2RuntimeError as e:
                raise UpdateFailed(str(e)) from e
        self._apply_gen2_result(result)
        self.async_set_updated_data(self._coordinator_payload())

    async def async_gen1_start_watering(self, duration_sec: int) -> None:
        """Start a manual run on the gen1 timer's single port, then refresh state."""
        async with self._session_lock:
            try:
                result = await async_gen1_manual_start(
                    self.hass, self._gen1_session_params(), int(duration_sec)
                )
            except Gen1RuntimeError as e:
                raise UpdateFailed(str(e)) from e
        self._apply_gen1_result(result)
        self.async_set_updated_data(self._coordinator_payload())
        self.schedule_status_refresh_after_run(int(duration_sec))

    async def async_gen1_stop_watering(self) -> None:
        """Stop watering on the gen1 timer, then refresh state."""
        async with self._session_lock:
            try:
                result = await async_gen1_stop_watering(self.hass, self._gen1_session_params())
            except Gen1RuntimeError as e:
                raise UpdateFailed(str(e)) from e
        self._apply_gen1_result(result)
        self.async_set_updated_data(self._coordinator_payload())

    async def _async_update_gen1(self) -> dict:
        async with self._session_lock:
            try:
                result = await async_read_gen1_status(self.hass, self._gen1_session_params())
            except Gen1RuntimeError as e:
                raise UpdateFailed(str(e)) from e
        self._apply_gen1_result(result)
        return self._coordinator_payload()

    async def _async_update_gen2(self) -> dict:
        async with self._session_lock:
            try:
                result = await async_read_gen2_status(self.hass, self._gen2_session_params())
            except Gen2RuntimeError as e:
                raise UpdateFailed(str(e)) from e
        self._apply_gen2_result(result)
        log_ble_merged(self.address, self._last_message)
        return self._coordinator_payload()

    async def _async_update_data(self) -> dict:
        try:
            if self.is_gen1:
                return await self._async_update_gen1()
            return await self._async_update_gen2()
        except UpdateFailed:
            raise
        except Exception as e:
            raise UpdateFailed(str(e)) from e
