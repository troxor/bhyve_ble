"""
Post-provision verification for timers.

Enable verbose onboarding logs in Home Assistant ``configuration.yaml``::

    logger:
      logs:
        custom_components.bhyve_ble.onboarding: debug
        custom_components.bhyve_ble.logging: debug
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from bleak_retry_connector import close_stale_connections_by_address

from .ble import BleProvisionOptions, async_provision_with_network_key
from .const import GENERATION_GEN1
from .device_profile import DeviceBleProfile, device_ble_profile
from .gen1_codec import gen1_status_snapshot_verified
from .gen1_runtime import Gen1BleSessionParams, async_gen1_onboard_session, async_read_gen1_status
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


class BhyveOnboardingError(Exception):
    """Raised when we cannot confirm the device after GATT provisioning."""


async def async_verify_gen1_device_communication(
    hass: HomeAssistant,
    address: str,
    network_key_16: bytes,
    mesh_device_id: int,
    *,
    ble_profile: DeviceBleProfile | None = None,
) -> dict[str, Any]:
    """
    Verify existing gen1 credentials with a status-only session (no provision).

    Skips ``network_char`` provisioning and onboard scripts.
    """
    profile = ble_profile or device_ble_profile(GENERATION_GEN1)
    mid = int(mesh_device_id)
    _LOGGER.debug(
        "[%s] gen1 verify start mesh_id=%s tx_delay_ms=%s link_type=0x%02x",
        address,
        mid,
        profile.tx_delay_ms,
        profile.link_msg_type,
    )

    params = Gen1BleSessionParams(
        address=address,
        network_key_16=network_key_16,
        mesh_device_id=mid,
        ble_profile=profile,
    )
    try:
        snapshot = await async_read_gen1_status(hass, params)
    except Exception as e:
        raise BhyveOnboardingError(str(e)) from e

    if not gen1_status_snapshot_verified(snapshot):
        _LOGGER.warning(
            "[%s] gen1 verify failed mesh_id=%s snapshot_keys=%s",
            address,
            mid,
            sorted(snapshot),
        )
        msg = "Timed out waiting for gen1 status from the timer (check mesh ID and network key)."
        raise BhyveOnboardingError(msg)

    _LOGGER.debug("[%s] gen1 verify ok snapshot_keys=%s", address, sorted(snapshot))
    return snapshot


async def async_onboard_gen1_device(
    hass: HomeAssistant,
    address: str,
    network_key_16: bytes,
    mesh_device_id: int,
    *,
    ble_profile: DeviceBleProfile | None = None,
) -> dict[str, Any]:
    """
    First-time gen1 bind: provision with mesh prefix, onboard script, status read.

    Returns a gen1 ``status_snapshot`` dict (must include ``device_info``).
    """
    profile = ble_profile or device_ble_profile(GENERATION_GEN1)
    mid = int(mesh_device_id)
    _LOGGER.debug(
        "[%s] gen1 onboard start mesh_id=%s tx_delay_ms=%s link_type=0x%02x",
        address,
        mid,
        profile.tx_delay_ms,
        profile.link_msg_type,
    )

    await async_provision_with_network_key(
        hass,
        address,
        network_key_16,
        BleProvisionOptions(tx_delay_ms=profile.tx_delay_ms, mesh_device_id=mid),
    )

    params = Gen1BleSessionParams(
        address=address,
        network_key_16=network_key_16,
        mesh_device_id=mid,
        ble_profile=profile,
    )
    try:
        snapshot = await async_gen1_onboard_session(hass, params)
    except Exception as e:
        raise BhyveOnboardingError(str(e)) from e

    if "device_info" not in snapshot:
        msg = "Timed out waiting for gen1 device_info from the timer."
        raise BhyveOnboardingError(msg)

    _LOGGER.debug("[%s] gen1 onboard ok snapshot_keys=%s", address, sorted(snapshot))
    return snapshot


def _orbit_onboard_notify_is_complete(decoded: dict) -> bool:
    msg = decoded.get("message") or {}
    return bool(msg.get("deviceInfo") or msg.get("deviceStatusInfo"))


async def _async_send_orbit_onboard_queries(
    transport: BhyveBleTransport,
    address: str,
    link_msg_type: int,
) -> None:
    info_plain = encode_get_device_info_plaintext()
    log_ble_tx(address, link_msg_type, info_plain)
    await transport.async_send_plaintext(link_msg_type, info_plain)

    status_plain = encode_get_device_status_info_plaintext()
    log_ble_tx(address, link_msg_type, status_plain)
    await transport.async_send_plaintext(link_msg_type, status_plain)


async def _async_orbit_verify_session(
    hass: HomeAssistant,
    address: str,
    network_key_16: bytes,
    profile: DeviceBleProfile,
    timeout: float,
) -> dict:
    transport = BhyveBleTransport(hass, address, network_key_16)
    last_msg: dict | None = None
    done = asyncio.Event()
    notify_count = 0

    async def on_notify(msg_type: int, plaintext: bytes) -> None:
        nonlocal last_msg, notify_count
        notify_count += 1
        try:
            decoded = decode_orbit_ble_plaintext(plaintext)
        except Exception as e:  # noqa: BLE001
            log_ble_rx_decode_failed(address, msg_type, plaintext, e)
            return

        log_ble_rx(address, msg_type, plaintext, decoded)
        oneof = (decoded.get("_framing") or {}).get("oneof") or "?"
        if _orbit_onboard_notify_is_complete(decoded):
            _LOGGER.debug(
                "[%s] onboarding verify matched oneof=%s message_keys=%s (notify #%d)",
                address,
                oneof,
                sorted((decoded.get("message") or {}).keys()),
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
        await close_stale_connections_by_address(address)
        await transport.async_connect_and_subscribe(on_notify, tx_delay_ms=profile.tx_delay_ms)
        _LOGGER.debug(
            "[%s] onboarding connected, sending getDeviceInfo + getDeviceStatusInfo", address
        )
        await _async_send_orbit_onboard_queries(transport, address, profile.link_msg_type)
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


async def async_verify_device_communication(
    hass: HomeAssistant,
    address: str,
    network_key_16: bytes,
    *,
    timeout: float = 20.0,
    ble_profile: DeviceBleProfile | None = None,
) -> dict:
    """
    Connect (fresh AES session), request status/info, wait for a decoded Orbit message.

    Gen1 timers must use :func:`async_onboard_gen1_device` or
    :func:`async_verify_gen1_device_communication` instead.

    Returns a ``decode_orbit_ble_plaintext``-style dict (includes ``message`` / ``_framing``).
    """
    profile = ble_profile or device_ble_profile(None)
    if profile.generation == GENERATION_GEN1:
        msg = "gen1 timers use async_onboard_gen1_device, not Orbit verify"
        raise ValueError(msg)

    _LOGGER.debug(
        "[%s] onboarding verify start timeout=%.1fs tx_delay_ms=%s link_msg_type=0x%02x",
        address,
        timeout,
        profile.tx_delay_ms,
        profile.link_msg_type,
    )
    return await _async_orbit_verify_session(hass, address, network_key_16, profile, timeout)
