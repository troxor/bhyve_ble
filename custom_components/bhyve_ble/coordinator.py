from __future__ import annotations

import asyncio
import base64
import logging
from typing import TYPE_CHECKING

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_DEVICES, CONF_NETWORK_KEY_B64, DOMAIN
from .device_profile import device_ble_profile_from_meta
from .logging import log_ble_merged, log_ble_rx, log_ble_rx_decode_failed, log_ble_tx
from .orbit_codec import (
    decode_orbit_ble_plaintext,
    deep_merge_partial_proto_dict,
    encode_get_device_info_plaintext,
    encode_get_device_status_info_plaintext,
    parse_num_stations_from_decoded,
)
from .transport import BhyveBleTransport, BhyveBleTransportError

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
        self._network_key16 = base64.b64decode(entry.data[CONF_NETWORK_KEY_B64])
        device_meta = (entry.data.get(CONF_DEVICES) or {}).get(address) or {}
        profile = device_ble_profile_from_meta(device_meta)
        self._link_msg_type = profile.link_msg_type
        self._tx_delay_ms = profile.tx_delay_ms
        self._transport = BhyveBleTransport(hass, self.address, self._network_key16)
        self._last_message: dict | None = None
        self._device_info: dict | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{self.address}",
            update_interval=update_interval,
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
                n = int(self._device_info["numStations"])
                return max(1, min(n, 64))
            except TypeError, ValueError:
                pass
        n = parse_num_stations_from_decoded(self._last_message)
        if n is not None:
            return max(1, min(int(n), 64))
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
                merged_msg[key] = deep_merge_partial_proto_dict(prev_b, new_b)
        out = {**new, "message": merged_msg}
        out["_framing"] = new.get("_framing") or prev.get("_framing")
        return out

    async def async_send_orbit_plaintext(self, plaintext: bytes) -> None:
        if not self._transport.is_connected:
            await self._transport.async_connect_and_subscribe(
                self._handle_notify, tx_delay_ms=self._tx_delay_ms
            )
        log_ble_tx(self.address, self._link_msg_type, plaintext)
        await self._transport.async_send_plaintext(self._link_msg_type, plaintext)

    async def async_shutdown(self) -> None:
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
        self.async_set_updated_data(
            self.data | {"last_message": self._last_message, "num_stations": self.num_stations}
            if self.data
            else {"last_message": self._last_message, "num_stations": self.num_stations}
        )

    async def _async_update_data(self) -> dict:
        try:
            if not self._transport.is_connected:
                await self._transport.async_connect_and_subscribe(
                    self._handle_notify, tx_delay_ms=self._tx_delay_ms
                )

            await self._transport.async_send_plaintext(
                self._link_msg_type, encode_get_device_info_plaintext()
            )
            await asyncio.sleep(0.2)
            await self._transport.async_send_plaintext(
                self._link_msg_type, encode_get_device_status_info_plaintext()
            )
            await asyncio.sleep(0.35)

            log_ble_merged(self.address, self._last_message)

            if self._last_message:
                di = (self._last_message.get("message") or {}).get("deviceInfo")
                if di:
                    self._device_info = di

            return {
                "address": self.address,
                "name": self.name,
                "last_message": self._last_message,
                "num_stations": self.num_stations,
            }
        except (BhyveBleTransportError, Exception) as e:
            raise UpdateFailed(str(e)) from e
