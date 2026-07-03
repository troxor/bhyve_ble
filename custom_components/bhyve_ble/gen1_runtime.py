from __future__ import annotations

import asyncio
import logging
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .pybhyve.constants import (
    COMMAND_LISTEN_S,
    GEN1_STATUS_LISTEN_S,
    START_CONFIRM_LISTEN_S,
)
from .logging import log_ble_rx, log_ble_rx_decode_failed, log_ble_tx
from .transport import BhyveBleTransport, BhyveBleTransportError
from .pybhyve.gen1_codec import decode_gen1_plaintext
from .pybhyve.gen1_ops import (
    run_gen1_manual_start,
    run_gen1_onboard,
    run_gen1_status_session,
    run_gen1_stop_watering,
)
from .pybhyve.gen1_session import Gen1Session

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.core import HomeAssistant

    from .device_profile import DeviceBleProfile

_LOGGER = logging.getLogger(__name__)


class Gen1RuntimeError(Exception):
    """Raised when a gen1 BLE session fails."""


@dataclass(frozen=True, slots=True)
class Gen1BleSessionParams:
    address: str
    network_key_16: bytes
    device_id: int
    ble_profile: DeviceBleProfile


@dataclass(frozen=True, slots=True)
class Gen1SessionResult:
    """Outcome of one gen1 BLE session: status snapshot plus any assigned device id."""

    snapshot: dict[str, Any]
    assigned_device_id: int | None = None


async def _async_run_gen1_ble_session(
    hass: HomeAssistant,
    params: Gen1BleSessionParams,
    session_runner: Callable[[Gen1Session, int], Awaitable[None]],
    *,
    listen_seconds: float = GEN1_STATUS_LISTEN_S,
) -> Gen1SessionResult:
    """Connect, subscribe, run an operation, listen briefly, disconnect."""
    mid = int(params.device_id)
    transport = BhyveBleTransport(hass, params.address, params.network_key_16)
    magic = struct.pack("<H", mid)
    link_t = params.ble_profile.link_msg_type
    gen1: Gen1Session | None = None

    async def send_plaintext(pt: bytes, label: str) -> None:
        _LOGGER.debug("[%s] gen1 tx %s", params.address, label)
        log_ble_tx(params.address, link_t, pt)
        await transport.async_send_plaintext(link_t, pt)

    async def on_notify(msg_type: int, plaintext: bytes) -> None:
        if gen1 is None:
            return
        try:
            dec = decode_gen1_plaintext(plaintext)
        except Exception as e:  # noqa: BLE001
            log_ble_rx_decode_failed(params.address, msg_type, plaintext, e)
            return
        log_ble_rx(params.address, msg_type, plaintext, dec)
        gen1.on_notify_plaintext(plaintext)

    try:
        await transport.async_connect_and_subscribe(
            on_notify, tx_delay_ms=params.ble_profile.tx_delay_ms
        )
        gen1 = Gen1Session(magic=magic, send_plaintext=send_plaintext)
        await session_runner(gen1, mid)
        if listen_seconds > 0:
            await asyncio.sleep(listen_seconds)
        return Gen1SessionResult(
            snapshot=gen1.status_snapshot,
            assigned_device_id=gen1.assigned_device_id,
        )
    except BhyveBleTransportError as e:
        _LOGGER.debug("[%s] gen1 session transport error: %s", params.address, e)
        raise Gen1RuntimeError(str(e)) from e
    finally:
        await transport.async_disconnect()
        _LOGGER.debug("[%s] gen1 session disconnected", params.address)


async def async_read_gen1_status(
    hass: HomeAssistant,
    params: Gen1BleSessionParams,
) -> Gen1SessionResult:
    async def run_status(gen1: Gen1Session, device_id: int) -> None:
        await run_gen1_status_session(gen1, device_id)

    return await _async_run_gen1_ble_session(hass, params, run_status)


async def async_gen1_manual_start(
    hass: HomeAssistant,
    params: Gen1BleSessionParams,
    duration_sec: int,
) -> Gen1SessionResult:
    async def run_start(gen1: Gen1Session, device_id: int) -> None:
        await run_gen1_manual_start(gen1, device_id, duration_sec)

    return await _async_run_gen1_ble_session(
        hass, params, run_start, listen_seconds=START_CONFIRM_LISTEN_S
    )


async def async_gen1_stop_watering(
    hass: HomeAssistant,
    params: Gen1BleSessionParams,
) -> Gen1SessionResult:
    async def run_stop(gen1: Gen1Session, device_id: int) -> None:
        await run_gen1_stop_watering(gen1, device_id)

    return await _async_run_gen1_ble_session(
        hass, params, run_stop, listen_seconds=COMMAND_LISTEN_S
    )


async def async_gen1_onboard_session(
    hass: HomeAssistant,
    params: Gen1BleSessionParams,
) -> Gen1SessionResult:
    async def run_onboard(gen1: Gen1Session, device_id: int) -> None:
        await run_gen1_onboard(gen1, device_id)

    return await _async_run_gen1_ble_session(hass, params, run_onboard)


__all__ = [
    "Gen1BleSessionParams",
    "Gen1RuntimeError",
    "Gen1SessionResult",
    "async_gen1_manual_start",
    "async_gen1_onboard_session",
    "async_gen1_stop_watering",
    "async_read_gen1_status",
]
