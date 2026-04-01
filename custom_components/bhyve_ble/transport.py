from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import async_timeout
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, BleakNotFoundError, establish_connection

from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.core import HomeAssistant

from .const import AES_CHAR_UUID, READ_CHAR_UUID, WRITE_CHAR_UUID
from .link_crypto import SessionKeys, build_data_frame, parse_data_frame
from .provisioning import build_aes_char_write_payload, derive_from_aes_char_exchange

_LOGGER = logging.getLogger(__name__)

NotifyCallback = Callable[[int, bytes], Awaitable[None]]


class BhyveBleTransportError(Exception):
    pass


class BhyveBleTransport:
    def __init__(self, hass: HomeAssistant, address: str, network_key16: bytes) -> None:
        self.hass = hass
        self.address = address
        self.network_key16 = network_key16

        self._client: BleakClientWithServiceCache | None = None
        self._keys: SessionKeys | None = None
        self._notify_cb: NotifyCallback | None = None
        self._write_lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    async def async_connect_and_subscribe(
        self,
        notify_cb: NotifyCallback,
        *,
        timeout: float = 30.0,
        tx_delay_ms: int = 0,
    ) -> None:
        self._notify_cb = notify_cb
        ble_device = async_ble_device_from_address(self.hass, self.address)
        if ble_device is None:
            raise BhyveBleTransportError(f"BLE device not found for address {self.address}")

        addr = self.address

        def _ble_device_callback():
            d = async_ble_device_from_address(self.hass, addr)
            if d is None:
                raise BleakNotFoundError(f"BLE device not found for address {addr}")
            return d

        try:
            async with async_timeout.timeout(timeout):
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    ble_device,
                    ble_device.name or addr,
                    ble_device_callback=_ble_device_callback,
                    max_attempts=4,
                )
        except (BleakError, asyncio.TimeoutError, OSError) as e:
            raise BhyveBleTransportError(f"connect failed: {e}") from e

        self._client = client

        # Session AES init: re-derive iv/counters on each connect.
        write20 = build_aes_char_write_payload(tx_delay_ms)
        await client.write_gatt_char(AES_CHAR_UUID, write20, response=True)
        read20 = await client.read_gatt_char(AES_CHAR_UUID)
        derived = derive_from_aes_char_exchange(write20, read20)
        self._keys = SessionKeys(
            network_key16=self.network_key16,
            iv12=derived.iv12,
            enc_ctr=derived.enc_ctr,
            dec_ctr=derived.dec_ctr,
        )

        await client.start_notify(READ_CHAR_UUID, self._on_notify)

    async def async_disconnect(self) -> None:
        if not self._client:
            return
        try:
            await self._client.stop_notify(READ_CHAR_UUID)
        except Exception:  # noqa: BLE001
            pass
        try:
            await self._client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        self._client = None
        self._keys = None

    async def async_send_plaintext(self, msg_type: int, plaintext: bytes) -> None:
        if not self._client or not self._keys:
            raise BhyveBleTransportError("not connected")
        async with self._write_lock:
            frame, new_ctr = build_data_frame(
                msg_type,
                plaintext,
                key16=self._keys.network_key16,
                iv12=self._keys.iv12,
                enc_ctr=self._keys.enc_ctr,
            )
            self._keys = SessionKeys(
                network_key16=self._keys.network_key16,
                iv12=self._keys.iv12,
                enc_ctr=new_ctr,
                dec_ctr=self._keys.dec_ctr,
            )
            await self._client.write_gatt_char(WRITE_CHAR_UUID, frame, response=False)

    def _on_notify(self, _handle: int, data: bytearray) -> None:
        if not self._keys:
            return
        frame = bytes(data)
        try:
            msg_type, plaintext, new_ctr = parse_data_frame(
                frame,
                key16=self._keys.network_key16,
                iv12=self._keys.iv12,
                dec_ctr=self._keys.dec_ctr,
            )
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("notify parse failed (%d bytes): %s", len(frame), e)
            return

        self._keys = SessionKeys(
            network_key16=self._keys.network_key16,
            iv12=self._keys.iv12,
            enc_ctr=self._keys.enc_ctr,
            dec_ctr=new_ctr,
        )

        if self._notify_cb:
            asyncio.create_task(self._notify_cb(msg_type, plaintext))

