from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    BLE_COMMAND_LISTEN_S,
    BLE_QUERY_GAP_S,
    BLE_STATUS_LISTEN_S,
    BLE_STATUS_SETTLE_S,
    CONF_DEVICE_GENERATION,
    DEFAULT_MANUAL_WATER_RUN_SEC,
    DOMAIN,
    GENERATION_GEN1,
    GEN2_STATUS_LISTEN_S,
)
from .device_credentials import device_meta, device_network_key, mesh_device_id
from .device_profile import device_ble_profile_from_meta
from .gen1_codec import (
    gen1_device_info_for_registry,
    gen1_is_watering,
)
from .gen1_runtime import (
    Gen1BleSessionParams,
    Gen1RuntimeError,
    async_gen1_manual_start,
    async_gen1_stop_watering,
    async_read_gen1_status,
)
from .logging import log_ble_merged, log_ble_rx, log_ble_rx_decode_failed, log_ble_tx
from .orbit_codec import (
    MANUAL_WATER_RUN_SEC_MAX,
    MANUAL_WATER_RUN_SEC_MIN,
    decode_orbit_ble_plaintext,
    deep_merge_device_status_info,
    deep_merge_partial_proto_dict,
    encode_get_battery_status_plaintext,
    encode_get_device_info_plaintext,
    encode_get_device_status_info_plaintext,
    normalize_orbit_message_for_status,
    normalize_num_stations,
    parse_num_stations_from_decoded,
)
from .transport import BhyveBleTransport, BhyveBleTransportError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
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
        self._generation = meta.get(CONF_DEVICE_GENERATION, profile.generation)
        self._mesh_device_id = mesh_device_id(entry, address)
        self._network_key16 = device_network_key(entry, address)
        self._link_msg_type = profile.link_msg_type
        self._tx_delay_ms = profile.tx_delay_ms
        self._transport = BhyveBleTransport(hass, self.address, self._network_key16)
        self._last_message: dict | None = None
        self._gen1_snapshot: dict | None = None
        self._device_info: dict | None = None
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
        """Manual watering duration for ``station_id`` (seconds)."""
        raw = self._station_run_sec.get(station_id, DEFAULT_MANUAL_WATER_RUN_SEC)
        return max(MANUAL_WATER_RUN_SEC_MIN, min(int(raw), MANUAL_WATER_RUN_SEC_MAX))

    def set_station_manual_run_seconds(self, station_id: int, seconds: int) -> None:
        self._station_run_sec[station_id] = max(
            MANUAL_WATER_RUN_SEC_MIN,
            min(int(seconds), MANUAL_WATER_RUN_SEC_MAX),
        )

    @property
    def is_gen1(self) -> bool:
        return self._generation == GENERATION_GEN1

    def _gen1_params(self) -> Gen1BleSessionParams:
        if self._mesh_device_id is None:
            msg = f"gen1 timer {self.address} is missing mesh_device_id"
            raise Gen1RuntimeError(msg)
        return Gen1BleSessionParams(
            address=self.address,
            network_key_16=self._network_key16,
            mesh_device_id=self._mesh_device_id,
            ble_profile=device_ble_profile_from_meta(
                device_meta(self.entry, self.address),
            ),
        )

    @property
    def orbit_device_info(self) -> dict | None:
        """Latest decoded ``deviceInfo`` submessage from the timer, if any."""
        return self._device_info

    @property
    def num_stations(self) -> int:
        """Number of valve ports from ``deviceInfo.numStations`` (default 1 until known)."""
        if self._device_info and self._device_info.get("numStations") is not None:
            try:
                return normalize_num_stations(int(self._device_info["numStations"]))
            except TypeError, ValueError:
                pass
        n = parse_num_stations_from_decoded(self._last_message)
        if n is not None:
            return n
        return 1

    def _merge_orbit_decoded(self, prev: dict | None, new: dict) -> dict:
        """Merge oneof branches so ``deviceInfo`` and ``deviceStatusInfo`` can coexist."""
        if not prev:
            return new
        prev_msg = prev.get("message") or {}
        new_msg = new.get("message") or {}
        merged_msg = {**prev_msg, **new_msg}
        for key in ("deviceInfo", "deviceStatusInfo"):
            prev_b = prev_msg.get(key)
            new_b = new_msg.get(key)
            if isinstance(prev_b, dict) and isinstance(new_b, dict):
                if key == "deviceStatusInfo":
                    merged_msg[key] = deep_merge_device_status_info(prev_b, new_b)
                else:
                    merged_msg[key] = deep_merge_partial_proto_dict(prev_b, new_b)
        out = {**new, "message": normalize_orbit_message_for_status(merged_msg)}
        out["_framing"] = new.get("_framing") or prev.get("_framing")
        return out

    def _coordinator_payload(self) -> dict:
        return {
            "address": self.address,
            "name": self.name,
            "last_message": self._last_message,
            "gen1_snapshot": self._gen1_snapshot,
            "is_gen1": self.is_gen1,
            "num_stations": self.num_stations,
        }

    def _sync_device_info_from_gen1_snapshot(self, snapshot: dict) -> None:
        mapped = gen1_device_info_for_registry(snapshot)
        if mapped:
            self._device_info = mapped
            return
        di = snapshot.get("device_info")
        if di:
            self._device_info = {"numStations": di.get("num_stations", 1)}

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
        """Schedule mid-run and post-run coordinator polls (gen1 + gen2)."""
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

    async def async_run_ble_session(
        self,
        body: Callable[[], Awaitable[None]],
        *,
        listen_seconds: float = 0.0,
    ) -> None:
        """
        Connect, run ``body`` (writes while subscribed), optionally wait for NOTIFYs, disconnect.

        One session per operation so the timer radio is not held open between polls.
        """
        async with self._session_lock:
            try:
                await self._transport.async_connect_and_subscribe(
                    self._handle_notify,
                    tx_delay_ms=self._tx_delay_ms,
                )
                await body()
                if listen_seconds > 0:
                    await asyncio.sleep(listen_seconds)
            finally:
                await self._transport.async_disconnect()

    async def async_send_orbit_command(
        self,
        plaintext: bytes,
        *,
        listen_seconds: float = BLE_COMMAND_LISTEN_S,
    ) -> None:
        """Send one application plaintext inside a short BLE session."""
        if self.is_gen1:
            msg = "gen1 timers use async_gen1_manual_start / async_gen1_stop_watering"
            raise Gen1RuntimeError(msg)

        async def body() -> None:
            log_ble_tx(self.address, self._link_msg_type, plaintext)
            await self._transport.async_send_plaintext(self._link_msg_type, plaintext)

        await self.async_run_ble_session(body, listen_seconds=listen_seconds)
        self._sync_device_info_from_last_message()
        self.async_set_updated_data(self._coordinator_payload())

    async def async_gen1_manual_start(self, duration_sec: int) -> None:
        async with self._session_lock:
            try:
                self._gen1_snapshot = await async_gen1_manual_start(
                    self.hass, self._gen1_params(), duration_sec
                )
            except Gen1RuntimeError as e:
                raise BhyveBleTransportError(str(e)) from e
        self._sync_device_info_from_gen1_snapshot(self._gen1_snapshot or {})
        self.async_set_updated_data(self._coordinator_payload())
        if gen1_is_watering(self._gen1_snapshot):
            self.schedule_status_refresh_after_run(duration_sec)

    async def async_gen1_stop_watering(self) -> None:
        async with self._session_lock:
            try:
                self._gen1_snapshot = await async_gen1_stop_watering(self.hass, self._gen1_params())
            except Gen1RuntimeError as e:
                raise BhyveBleTransportError(str(e)) from e
        self._sync_device_info_from_gen1_snapshot(self._gen1_snapshot or {})
        self.async_set_updated_data(self._coordinator_payload())

    def gen1_port_is_watering(self, station_id: int = 0) -> bool:
        if station_id != 0:
            return False
        return gen1_is_watering(self._gen1_snapshot)

    async def async_shutdown(self) -> None:
        self._cancel_deferred_refresh_callbacks()
        await self._transport.async_disconnect()

    async def _handle_notify(self, msg_type: int, plaintext: bytes) -> None:
        try:
            decoded = decode_orbit_ble_plaintext(plaintext)
        except Exception as e:  # noqa: BLE001
            log_ble_rx_decode_failed(self.address, msg_type, plaintext, e)
            return
        decoded["_link"] = {"msg_type": msg_type, "bytes": len(plaintext)}
        log_ble_rx(self.address, msg_type, plaintext, decoded)
        self._last_message = self._merge_orbit_decoded(self._last_message, decoded)
        log_ble_merged(self.address, self._last_message)
        di = (self._last_message.get("message") or {}).get("deviceInfo")
        if di:
            self._device_info = di
        self.async_set_updated_data(self._coordinator_payload())

    async def _async_poll_device(self) -> None:
        await self._transport.async_send_plaintext(
            self._link_msg_type, encode_get_device_status_info_plaintext()
        )
        await asyncio.sleep(BLE_STATUS_SETTLE_S)
        await self._transport.async_send_plaintext(
            self._link_msg_type, encode_get_device_info_plaintext()
        )
        await asyncio.sleep(BLE_QUERY_GAP_S)
        await self._transport.async_send_plaintext(
            self._link_msg_type, encode_get_battery_status_plaintext()
        )
        await asyncio.sleep(BLE_STATUS_SETTLE_S)

    async def _async_update_data(self) -> dict:
        try:
            if self.is_gen1:
                async with self._session_lock:
                    self._gen1_snapshot = await async_read_gen1_status(
                        self.hass, self._gen1_params()
                    )
                self._sync_device_info_from_gen1_snapshot(self._gen1_snapshot or {})
                return self._coordinator_payload()

            async def body() -> None:
                await self._async_poll_device()

            await self.async_run_ble_session(
                body, listen_seconds=GEN2_STATUS_LISTEN_S
            )
            log_ble_merged(self.address, self._last_message)
            self._sync_device_info_from_last_message()
            return self._coordinator_payload()
        except (BhyveBleTransportError, Gen1RuntimeError, Exception) as e:
            raise UpdateFailed(str(e)) from e
