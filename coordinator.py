from __future__ import annotations

import asyncio
import base64
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_NETWORK_KEY_B64, DOMAIN
from .orbit_codec import (
    decode_orbit_ble_plaintext,
    encode_get_device_info_plaintext,
    encode_get_device_status_info_plaintext,
    parse_num_stations_from_decoded,
)
from .transport import BhyveBleTransport, BhyveBleTransportError

_LOGGER = logging.getLogger(__name__)

ORBIT_APP_MSG_TYPE = 0x11


class BhyveBleCoordinator(DataUpdateCoordinator[dict]):
    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        address: str,
        name: str,
    ) -> None:
        self.entry = entry
        self.address = address
        self.name = name
        self._network_key16 = base64.b64decode(entry.data[CONF_NETWORK_KEY_B64])
        self._transport = BhyveBleTransport(hass, self.address, self._network_key16)
        self._last_message: dict | None = None
        self._device_info: dict | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{self.address}",
            update_interval=timedelta(seconds=30),
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
            except (TypeError, ValueError):
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
        out = {**new, "message": merged_msg}
        out["_framing"] = new.get("_framing") or prev.get("_framing")
        return out

    async def async_send_orbit_plaintext(self, plaintext: bytes) -> None:
        if not self._transport.is_connected:
            await self._transport.async_connect_and_subscribe(self._handle_notify)
        await self._transport.async_send_plaintext(ORBIT_APP_MSG_TYPE, plaintext)

    async def async_shutdown(self) -> None:
        await self._transport.async_disconnect()

    async def _handle_notify(self, msg_type: int, plaintext: bytes) -> None:
        try:
            decoded = decode_orbit_ble_plaintext(plaintext)
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("Failed to decode Orbit plaintext (%s): %s", self.address, e)
            return
        decoded["_link"] = {"msg_type": msg_type, "bytes": len(plaintext)}
        self._last_message = self._merge_orbit_decoded(self._last_message, decoded)
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
                await self._transport.async_connect_and_subscribe(self._handle_notify)

            await self._transport.async_send_plaintext(
                ORBIT_APP_MSG_TYPE, encode_get_device_info_plaintext()
            )
            await asyncio.sleep(0.2)
            await self._transport.async_send_plaintext(
                ORBIT_APP_MSG_TYPE, encode_get_device_status_info_plaintext()
            )
            await asyncio.sleep(0.35)

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
        except (BhyveBleTransportError, Exception) as e:  # noqa: BLE001
            raise UpdateFailed(str(e)) from e
