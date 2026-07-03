from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import async_timeout
from bleak.exc import BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakNotFoundError,
    close_stale_connections_by_address,
    establish_connection,
)
from homeassistant.components.bluetooth import async_ble_device_from_address

from .aes_handshake import async_complete_aes_char_handshake
from .pybhyve.constants import AES_CHAR_UUID, NOTIFY_CHAR_UUID, WRITE_CHAR_UUID
from .logging import log_ble_att_notify, log_ble_att_write_f
from .pybhyve.link_crypto import SessionKeys, build_data_frame, parse_inbound_data_frame

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

NotifyCallback = Callable[[int, bytes], Awaitable[None]]


class BhyveBleTransportError(Exception):
    pass


class BhyveBleTransport:
    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        network_key16: bytes,
    ) -> None:
        self.hass = hass
        self.address = address
        self.network_key16 = network_key16

        self._client: BleakClientWithServiceCache | None = None
        self._keys: SessionKeys | None = None
        self._notify_cb: NotifyCallback | None = None
        self._write_lock = asyncio.Lock()
        self._notify_tasks: set[asyncio.Task[None]] = set()

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
            msg = f"BLE device not found for address {self.address}"
            raise BhyveBleTransportError(msg)

        addr = self.address

        def _ble_device_callback():
            d = async_ble_device_from_address(self.hass, addr)
            if d is None:
                msg = f"BLE device not found for address {addr}"
                raise BleakNotFoundError(msg)
            return d

        try:
            await close_stale_connections_by_address(addr)
            async with async_timeout.timeout(timeout):
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    ble_device,
                    ble_device.name or addr,
                    ble_device_callback=_ble_device_callback,
                    max_attempts=4,
                )
        except (TimeoutError, BleakError, OSError) as e:
            msg = f"connect failed: {e}"
            raise BhyveBleTransportError(msg) from e

        self._client = client

        try:
            derived = await async_complete_aes_char_handshake(
                client,
                AES_CHAR_UUID,
                tx_delay_ms=tx_delay_ms,
            )
        except ValueError as e:
            msg = str(e)
            raise BhyveBleTransportError(msg) from e
        self._keys = SessionKeys(
            network_key16=self.network_key16,
            iv12=derived.iv12,
            enc_ctr=derived.enc_ctr,
            dec_ctr=derived.dec_ctr,
        )

        await client.start_notify(NOTIFY_CHAR_UUID, self._on_notify)

    async def async_disconnect(self) -> None:
        if not self._client:
            return
        try:
            await self._client.stop_notify(NOTIFY_CHAR_UUID)
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
            msg = "not connected"
            raise BhyveBleTransportError(msg)
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
            log_ble_att_write_f(self.address, frame, plaintext=plaintext)
            await self._write_gatt_frame(frame)

    async def _write_gatt_frame(self, frame: bytes) -> None:
        if not self._client:
            msg = "not connected"
            raise BhyveBleTransportError(msg)
        try:
            await self._client.write_gatt_char(WRITE_CHAR_UUID, frame, response=True)
        except BleakError as e:
            raise BhyveBleTransportError(str(e)) from e

    def _on_notify(self, _handle: int, data: bytearray) -> None:
        if not self._keys:
            return
        frame = bytes(data)
        if len(frame) < 4:
            return
        try:
            msg_type, plaintext, new_ctr, _skipped = parse_inbound_data_frame(
                frame,
                key16=self._keys.network_key16,
                iv12=self._keys.iv12,
                dec_ctr=self._keys.dec_ctr,
                accept_plaintext=None,
            )
        except Exception as e:  # noqa: BLE001
            log_ble_att_notify(
                self.address,
                frame,
                detail=f"parse failed: {e}",
            )
            _LOGGER.debug(
                "[%s] notify parse failed (%d bytes): %s frame_hex=%s",
                self.address,
                len(frame),
                e,
                frame.hex(),
            )
            return

        self._keys = SessionKeys(
            network_key16=self._keys.network_key16,
            iv12=self._keys.iv12,
            enc_ctr=self._keys.enc_ctr,
            dec_ctr=new_ctr,
        )

        log_ble_att_notify(self.address, frame, plaintext=plaintext)

        if self._notify_cb:
            task = asyncio.create_task(self._notify_cb(msg_type, plaintext))
            self._notify_tasks.add(task)
            task.add_done_callback(self._notify_tasks.discard)
