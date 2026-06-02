"""
Post-handshake check: confirm the timer responds to Orbit protobuf over the BLE data path.

Enable verbose onboarding logs in Home Assistant ``configuration.yaml``::

    logger:
      logs:
        custom_components.bhyve_ble.onboarding: debug
        custom_components.bhyve_ble.logging: debug
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .logging import log_ble_rx, log_ble_rx_decode_failed, log_ble_tx
from .orbit_codec import (
    decode_orbit_ble_plaintext,
    encode_get_device_info_plaintext,
    encode_get_device_status_info_plaintext,
)
from .transport import BhyveBleTransport, BhyveBleTransportError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

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
    """
    Connect (fresh AES session), request status/info, wait for a decoded Orbit message.

    Returns a ``decode_orbit_ble_plaintext``-style dict (includes ``message`` / ``_framing``).
    """
    transport = BhyveBleTransport(hass, address, network_key_16)
    last_msg: dict | None = None
    done = asyncio.Event()
    notify_count = 0

    _LOGGER.debug(
        "[%s] onboarding verify start timeout=%.1fs link_msg_type=0x%02x",
        address,
        timeout,
        ORBIT_APP_MSG_TYPE,
    )

    async def on_notify(msg_type: int, plaintext: bytes) -> None:
        nonlocal last_msg, notify_count
        notify_count += 1
        try:
            decoded = decode_orbit_ble_plaintext(plaintext)
        except Exception as e:  # noqa: BLE001
            log_ble_rx_decode_failed(address, msg_type, plaintext, e)
            return

        log_ble_rx(address, msg_type, plaintext, decoded)
        msg = decoded.get("message") or {}
        oneof = (decoded.get("_framing") or {}).get("oneof") or "?"
        if msg.get("deviceInfo") or msg.get("deviceStatusInfo"):
            _LOGGER.debug(
                "[%s] onboarding verify matched oneof=%s message_keys=%s (notify #%d)",
                address,
                oneof,
                sorted(msg.keys()),
                notify_count,
            )
            last_msg = decoded
            done.set()
        else:
            _LOGGER.debug(
                "[%s] onboarding notify #%d oneof=%s (waiting for deviceInfo/deviceStatusInfo)",
                address,
                notify_count,
                oneof,
            )

    try:
        await transport.async_connect_and_subscribe(on_notify)
        _LOGGER.debug(
            "[%s] onboarding connected, sending getDeviceInfo + getDeviceStatusInfo", address
        )

        info_plain = encode_get_device_info_plaintext()
        log_ble_tx(address, ORBIT_APP_MSG_TYPE, info_plain)
        await transport.async_send_plaintext(ORBIT_APP_MSG_TYPE, info_plain)

        status_plain = encode_get_device_status_info_plaintext()
        log_ble_tx(address, ORBIT_APP_MSG_TYPE, status_plain)
        await transport.async_send_plaintext(ORBIT_APP_MSG_TYPE, status_plain)

        try:
            await asyncio.wait_for(done.wait(), timeout=timeout)
        except TimeoutError as e:
            _LOGGER.debug(
                "[%s] onboarding verify timed out after %d notify frame(s)",
                address,
                notify_count,
            )
            msg = "Timed out waiting for deviceInfo or deviceStatusInfo from the timer."
            raise BhyveOnboardingError(msg) from e
    except BhyveBleTransportError as e:
        _LOGGER.debug("[%s] onboarding transport error: %s", address, e)
        raise BhyveOnboardingError(str(e)) from e
    finally:
        await transport.async_disconnect()
        _LOGGER.debug("[%s] onboarding disconnected", address)

    if last_msg is None:
        msg = "No usable Orbit message received from device."
        raise BhyveOnboardingError(msg)

    framing = last_msg.get("_framing") or {}
    _LOGGER.debug(
        "[%s] onboarding verify ok oneof=%s notify_count=%d",
        address,
        framing.get("oneof") or "?",
        notify_count,
    )
    return last_msg
