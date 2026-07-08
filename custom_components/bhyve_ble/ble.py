from __future__ import annotations

import logging
from dataclasses import dataclass
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
from .pybhyve.constants import AES_CHAR_UUID, NETWORK_CHAR_UUID
from .logging import log_ble_att_network_char
from .pybhyve.link_crypto import (
    AesHandshakeDerived,
    build_network_char_payload,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class BhyveBleProvisionError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class BleProvisionOptions:
    """Optional parameters for async_provision_with_network_key."""

    tx_delay_ms: int = 0
    device_id: int | None = None
    timeout: float = 30.0


async def async_provision_with_network_key(
    hass: HomeAssistant,
    address: str,
    network_key_16: bytes,
    options: BleProvisionOptions | None = None,
) -> AesHandshakeDerived:
    """Write network key to device and complete AES init on aes_char."""
    opts = options or BleProvisionOptions()
    if len(network_key_16) != 16:
        msg = "network key must be 16 bytes"
        raise BhyveBleProvisionError(msg)

    ble_device = async_ble_device_from_address(hass, address)
    if ble_device is None:
        msg = f"BLE device not found for address {address}"
        raise BhyveBleProvisionError(msg)

    def _ble_device_callback():
        d = async_ble_device_from_address(hass, address)
        if d is None:
            msg = f"BLE device not found for address {address}"
            raise BleakNotFoundError(msg)
        return d

    try:
        await close_stale_connections_by_address(address)
        async with async_timeout.timeout(opts.timeout):
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                ble_device.name or address,
                ble_device_callback=_ble_device_callback,
                max_attempts=4,
            )
    except (TimeoutError, BleakError, OSError) as e:
        msg = f"connect failed: {e}"
        raise BhyveBleProvisionError(msg) from e

    try:
        async with async_timeout.timeout(opts.timeout):
            net_payload = build_network_char_payload(network_key_16, opts.device_id)
            log_ble_att_network_char(address, net_payload)
            await client.write_gatt_char(NETWORK_CHAR_UUID, net_payload, response=True)

            try:
                return await async_complete_aes_char_handshake(
                    client,
                    AES_CHAR_UUID,
                    tx_delay_ms=opts.tx_delay_ms,
                    trace_address=address,
                )
            except ValueError as e:
                msg = str(e)
                raise BhyveBleProvisionError(msg) from e
    except (TimeoutError, BleakError, OSError) as e:
        raise BhyveBleProvisionError(str(e)) from e
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass
