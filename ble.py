from __future__ import annotations

import asyncio
import logging

from bleak.exc import BleakError
import async_timeout
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakNotFoundError,
    establish_connection,
)

from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.core import HomeAssistant

from .const import AES_CHAR_UUID, NETWORK_CHAR_UUID
from .provisioning import (
    AesHandshakeDerived,
    build_aes_char_write_payload,
    build_network_char_payload,
    derive_from_aes_char_exchange,
)

_LOGGER = logging.getLogger(__name__)


class BhyveBleProvisionError(Exception):
    pass


async def async_provision_with_network_key(
    hass: HomeAssistant,
    address: str,
    network_key_16: bytes,
    *,
    tx_delay_ms: int = 0,
    timeout: float = 30.0,
) -> AesHandshakeDerived:
    """Write shared network key to device and complete AES init on aes_char."""
    if len(network_key_16) != 16:
        raise BhyveBleProvisionError("network key must be 16 bytes")

    ble_device = async_ble_device_from_address(hass, address)
    if ble_device is None:
        raise BhyveBleProvisionError(f"BLE device not found for address {address}")

    def _ble_device_callback():
        d = async_ble_device_from_address(hass, address)
        if d is None:
            raise BleakNotFoundError(f"BLE device not found for address {address}")
        return d

    try:
        async with async_timeout.timeout(timeout):
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                ble_device.name or address,
                ble_device_callback=_ble_device_callback,
                max_attempts=4,
            )
    except (BleakError, asyncio.TimeoutError, OSError) as e:
        raise BhyveBleProvisionError(f"connect failed: {e}") from e

    try:
        # 1) network_char: LE16(1) || 16-byte key
        net_payload = build_network_char_payload(network_key_16)
        await client.write_gatt_char(NETWORK_CHAR_UUID, net_payload, response=True)

        # 2) aes_char: write 20 bytes, then read 20 bytes
        write20 = build_aes_char_write_payload(tx_delay_ms)
        await client.write_gatt_char(AES_CHAR_UUID, write20, response=True)

        # Some devices need a short delay before the read becomes valid.
        for attempt in range(10):
            read20 = await client.read_gatt_char(AES_CHAR_UUID)
            try:
                return derive_from_aes_char_exchange(write20, read20)
            except ValueError as e:
                _LOGGER.debug("aes_char read not valid yet (attempt %s): %s", attempt + 1, e)
                await asyncio.sleep(0.25)

        raise BhyveBleProvisionError("AES init response did not validate after retries")
    except (BleakError, OSError) as e:
        raise BhyveBleProvisionError(str(e)) from e
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass

