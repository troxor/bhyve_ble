"""Post-handshake check: confirm the timer responds to Orbit protobuf over the BLE data path."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.core import HomeAssistant

from .orbit_codec import decode_orbit_ble_plaintext, encode_get_device_info_plaintext, encode_get_device_status_info_plaintext
from .transport import BhyveBleTransport, BhyveBleTransportError

_LOGGER = logging.getLogger(__name__)

ORBIT_APP_MSG_TYPE = 0x11


class BhyveOnboardingError(Exception):
    """Raised when we cannot confirm the device after GATT provisioning."""


async def async_verify_device_communication(
    hass: HomeAssistant,
    address: str,
    network_key_16: bytes,
    *,
    timeout: float = 20.0,
) -> dict:
    """Connect (fresh AES session), request status/info, wait for a decoded Orbit message.

    Returns a ``decode_orbit_ble_plaintext``-style dict (includes ``message`` / ``_framing``).
    """
    transport = BhyveBleTransport(hass, address, network_key_16)
    last_msg: dict | None = None
    done = asyncio.Event()

    async def on_notify(_msg_type: int, plaintext: bytes) -> None:
        nonlocal last_msg
        try:
            decoded = decode_orbit_ble_plaintext(plaintext)
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("Onboarding notify decode skip: %s", e)
            return
        msg = decoded.get("message") or {}
        if msg.get("deviceInfo") or msg.get("deviceStatusInfo"):
            last_msg = decoded
            done.set()

    try:
        await transport.async_connect_and_subscribe(on_notify)
        await transport.async_send_plaintext(ORBIT_APP_MSG_TYPE, encode_get_device_info_plaintext())
        await transport.async_send_plaintext(ORBIT_APP_MSG_TYPE, encode_get_device_status_info_plaintext())
        try:
            await asyncio.wait_for(done.wait(), timeout=timeout)
        except asyncio.TimeoutError as e:
            raise BhyveOnboardingError(
                "Timed out waiting for deviceInfo or deviceStatusInfo from the timer."
            ) from e
    except BhyveBleTransportError as e:
        raise BhyveOnboardingError(str(e)) from e
    finally:
        await transport.async_disconnect()

    if last_msg is None:
        raise BhyveOnboardingError("No usable Orbit message received from device.")
    return last_msg
