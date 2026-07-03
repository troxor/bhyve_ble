"""
Gen2 BLE runtime: thin wrapper binding the HA transport to pybhyve's gen2 session ops.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .pybhyve.constants import (
    COMMAND_LISTEN_S,
    GEN2_STATUS_LISTEN_S,
    START_CONFIRM_LISTEN_S,
)
from .logging import log_ble_merged, log_ble_rx, log_ble_rx_decode_failed, log_ble_tx
from .transport import BhyveBleTransport, BhyveBleTransportError
from .pybhyve.gen2_ops import (
    run_gen2_manual_start,
    run_gen2_status_queries,
    run_gen2_stop_watering,
)
from .pybhyve.gen2_codec import decode_gen2_ble_plaintext, merge_gen2_decoded

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.core import HomeAssistant

    from .device_profile import DeviceBleProfile

_LOGGER = logging.getLogger(__name__)


class Gen2RuntimeError(Exception):
    """Raised when a gen2 BLE session fails."""


@dataclass(frozen=True, slots=True)
class Gen2BleSessionParams:
    address: str
    network_key_16: bytes
    ble_profile: DeviceBleProfile


@dataclass(frozen=True, slots=True)
class Gen2SessionResult:
    last_message: dict[str, Any] | None = None


async def _async_run_gen2_ble_session(
    hass: HomeAssistant,
    params: Gen2BleSessionParams,
    session_runner: Callable[[Callable[..., Awaitable[None]]], Awaitable[None]],
    *,
    listen_seconds: float = GEN2_STATUS_LISTEN_S,
) -> Gen2SessionResult:
    """Connect, subscribe, run an operation, listen briefly, disconnect."""
    transport = BhyveBleTransport(hass, params.address, params.network_key_16)
    link_t = params.ble_profile.link_msg_type
    last_message: dict[str, Any] | None = None

    async def send_plaintext(pt: bytes, *, label: str) -> None:
        _LOGGER.debug("[%s] gen2 tx %s", params.address, label)
        log_ble_tx(params.address, link_t, pt)
        await transport.async_send_plaintext(link_t, pt)

    async def on_notify(msg_type: int, plaintext: bytes) -> None:
        nonlocal last_message
        try:
            decoded = decode_gen2_ble_plaintext(plaintext)
        except Exception as e:  # noqa: BLE001
            log_ble_rx_decode_failed(params.address, msg_type, plaintext, e)
            return
        decoded["_link"] = {"msg_type": msg_type, "bytes": len(plaintext)}
        log_ble_rx(params.address, msg_type, plaintext, decoded)
        last_message = merge_gen2_decoded(last_message, decoded)
        log_ble_merged(params.address, last_message)

    try:
        await transport.async_connect_and_subscribe(
            on_notify, tx_delay_ms=params.ble_profile.tx_delay_ms
        )
        await session_runner(send_plaintext)
        if listen_seconds > 0:
            await asyncio.sleep(listen_seconds)
        return Gen2SessionResult(last_message=last_message)
    except BhyveBleTransportError as e:
        _LOGGER.debug("[%s] gen2 session transport error: %s", params.address, e)
        raise Gen2RuntimeError(str(e)) from e
    finally:
        await transport.async_disconnect()
        _LOGGER.debug("[%s] gen2 session disconnected", params.address)


async def async_read_gen2_status(
    hass: HomeAssistant,
    params: Gen2BleSessionParams,
) -> Gen2SessionResult:
    async def run_status(send: Callable[..., Awaitable[None]]) -> None:
        await run_gen2_status_queries(send)

    return await _async_run_gen2_ble_session(hass, params, run_status)


async def async_gen2_manual_start(
    hass: HomeAssistant,
    params: Gen2BleSessionParams,
    duration_sec: int,
    *,
    station_id: int = 0,
) -> Gen2SessionResult:
    async def run_start(send: Callable[..., Awaitable[None]]) -> None:
        await run_gen2_manual_start(send, duration_sec, station_id=station_id)

    return await _async_run_gen2_ble_session(
        hass, params, run_start, listen_seconds=START_CONFIRM_LISTEN_S
    )


async def async_gen2_stop_watering(
    hass: HomeAssistant,
    params: Gen2BleSessionParams,
) -> Gen2SessionResult:
    async def run_stop(send: Callable[..., Awaitable[None]]) -> None:
        await run_gen2_stop_watering(send)

    return await _async_run_gen2_ble_session(
        hass, params, run_stop, listen_seconds=COMMAND_LISTEN_S
    )


__all__ = [
    "Gen2BleSessionParams",
    "Gen2RuntimeError",
    "Gen2SessionResult",
    "async_gen2_manual_start",
    "async_gen2_stop_watering",
    "async_read_gen2_status",
]
