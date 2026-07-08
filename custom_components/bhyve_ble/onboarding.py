"""
Post-provision verification for timers.

Enable verbose onboarding logs in Home Assistant configuration.yaml:

    logger:
      logs:
        custom_components.bhyve_ble.onboarding: debug
        custom_components.bhyve_ble.logging: debug
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bleak_retry_connector import close_stale_connections_by_address

from .ble import BleProvisionOptions, async_provision_with_network_key
from .const import GENERATION_GEN1
from .device_profile import DeviceBleProfile, device_ble_profile
from .gen1_runtime import (
    Gen1BleSessionParams,
    Gen1RuntimeError,
    async_gen1_onboard_session,
    async_read_gen1_status,
)
from .logging import log_ble_rx, log_ble_rx_decode_failed, log_ble_tx
from .pybhyve import gen1_ops
from .pybhyve.gen1_codec import gen1_status_snapshot_verified
from .pybhyve.gen2_codec import (
    decode_gen2_ble_plaintext,
)
from .pybhyve.gen2_ops import run_gen2_onboard_queries
from .transport import BhyveBleTransport, BhyveBleTransportError

gen1_device_id = gen1_ops.gen1_device_id
gen1_network_key = gen1_ops.gen1_network_key

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)



class BhyveOnboardingError(Exception):
    """Raised when we cannot confirm the device after GATT provisioning."""


@dataclass(frozen=True, slots=True)
class Gen1OnboardResult:
    snapshot: dict[str, Any]
    assigned_device_id: int


def _gen1_profile(ble_profile: DeviceBleProfile | None) -> DeviceBleProfile:
    profile = ble_profile or device_ble_profile(GENERATION_GEN1)
    if profile.generation != GENERATION_GEN1:
        msg = "async gen1 onboarding requires a gen1 BLE profile"
        raise BhyveOnboardingError(msg)
    return profile


async def async_verify_gen1_device_communication(
    hass: HomeAssistant,
    address: str,
    network_key_16: bytes,
    device_id: int,
    *,
    ble_profile: DeviceBleProfile | None = None,
) -> dict[str, Any]:
    """
    Confirm an already-provisioned gen1 timer responds with its stored credentials.

    Runs a read-only status session (no network_char write, no onboard). Raises
    BhyveOnboardingError if the timer produces no decodable gen1 traffic.
    """
    profile = _gen1_profile(ble_profile)
    params = Gen1BleSessionParams(
        address=address,
        network_key_16=network_key_16,
        device_id=int(device_id),
        ble_profile=profile,
    )
    _LOGGER.debug("[%s] gen1 verify: status session device_id=%s", address, device_id)
    try:
        result = await async_read_gen1_status(hass, params)
    except Gen1RuntimeError as e:
        raise BhyveOnboardingError(str(e)) from e
    if not gen1_status_snapshot_verified(result.snapshot):
        msg = (
            "No usable gen1 status received from the timer. Check the device key and "
            "device ID, or re-pair the timer."
        )
        raise BhyveOnboardingError(msg)
    return result.snapshot


async def async_onboard_gen1_device(
    hass: HomeAssistant,
    address: str,
    network_key_16: bytes,
    device_id: int | None,
    *,
    ble_profile: DeviceBleProfile | None = None,
) -> Gen1OnboardResult:
    """
    First-time gen1 bind: write network_char, then run the onboard mesh-register script.

    device_id is the proposed client-generated session magic; when None one is
    generated. The device confirms its assigned BLE Device ID in the
    0xc4 NOTIFY, returned as Gen1OnboardResult.assigned_device_id.
    """
    profile = _gen1_profile(ble_profile)
    proposed = gen1_device_id(device_id)

    _LOGGER.debug(
        "[%s] gen1 onboard: provision network_char then register device_id=%s",
        address,
        proposed,
    )
    try:
        await async_provision_with_network_key(
            hass,
            address,
            network_key_16,
            BleProvisionOptions(tx_delay_ms=profile.tx_delay_ms, device_id=proposed),
        )
    except Exception as e:
        raise BhyveOnboardingError(str(e)) from e

    params = Gen1BleSessionParams(
        address=address,
        network_key_16=network_key_16,
        device_id=proposed,
        ble_profile=profile,
    )
    try:
        result = await async_gen1_onboard_session(hass, params)
    except Gen1RuntimeError as e:
        raise BhyveOnboardingError(str(e)) from e

    assigned = result.assigned_device_id
    if assigned is None and not gen1_status_snapshot_verified(result.snapshot):
        msg = (
            "Onboard finished but the timer did not confirm a device ID or send "
            "status. Keep the timer in pairing mode and close to the adapter, then retry."
        )
        raise BhyveOnboardingError(msg)
    return Gen1OnboardResult(
        snapshot=result.snapshot,
        assigned_device_id=int(assigned if assigned is not None else proposed),
    )


def _gen2_onboard_notify_is_complete(decoded: dict) -> bool:
    msg = decoded.get("message") or {}
    return bool(msg.get("deviceInfo") or msg.get("deviceStatusInfo"))


async def _async_send_gen2_onboard_queries(
    transport: BhyveBleTransport,
    address: str,
    link_msg_type: int,
) -> None:
    async def send(pt: bytes, *, label: str) -> None:  # noqa: ARG001
        log_ble_tx(address, link_msg_type, pt)
        await transport.async_send_plaintext(link_msg_type, pt)

    await run_gen2_onboard_queries(send)


async def _async_gen2_verify_session(
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
            decoded = decode_gen2_ble_plaintext(plaintext)
        except Exception as e:
            log_ble_rx_decode_failed(address, msg_type, plaintext, e)
            return

        log_ble_rx(address, msg_type, plaintext, decoded)
        oneof = (decoded.get("_framing") or {}).get("oneof") or "?"
        if _gen2_onboard_notify_is_complete(decoded):
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
        await _async_send_gen2_onboard_queries(transport, address, profile.link_msg_type)
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
        msg = "No usable Gen 2 message received from device."
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
    profile = ble_profile or device_ble_profile(None)

    _LOGGER.debug(
        "[%s] onboarding verify start timeout=%.1fs tx_delay_ms=%s link_msg_type=0x%02x",
        address,
        timeout,
        profile.tx_delay_ms,
        profile.link_msg_type,
    )
    return await _async_gen2_verify_session(hass, address, network_key_16, profile, timeout)
