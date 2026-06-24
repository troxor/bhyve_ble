"""Gen1 BLE runtime: status reads, manual start, and stop (shared with onboarding)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bleak_retry_connector import close_stale_connections_by_address

from .const import BLE_COMMAND_LISTEN_S, BLE_START_CONFIRM_LISTEN_S, BLE_STATUS_LISTEN_S
from .device_profile import DeviceBleProfile
from .gen1_codec import (
    decode_gen1_plaintext,
    format_gen1_watering_status,
    gen1_manual_start_plaintext,
    gen1_onboard_write_plaintexts,
    gen1_reconnect_write_plaintexts,
    gen1_session_poll_plaintext,
    gen1_status_probe_plaintext,
    gen1_status_session_plaintexts,
    gen1_stop_plaintext,
    mesh_prefix_bytes,
)
from .gen1_session import Gen1Session, run_gen1_session
from .logging import log_ble_rx, log_ble_rx_decode_failed, log_ble_tx
from .transport import BhyveBleTransport, BhyveBleTransportError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class Gen1RuntimeError(Exception):
    """Raised when a gen1 BLE session fails."""


@dataclass(frozen=True, slots=True)
class Gen1BleSessionParams:
    address: str
    network_key_16: bytes
    mesh_device_id: int
    ble_profile: DeviceBleProfile


async def async_run_gen1_status_session(
    gen1: Gen1Session,
    mesh_device_id: int,
    *,
    passive_poll: bool = True,
) -> None:
    """Run the lab-client ``status`` script (no onboard, no network_char write)."""
    mid = int(mesh_device_id)
    await run_gen1_session(gen1, gen1_status_session_plaintexts(mid))
    if passive_poll and format_gen1_watering_status(gen1.status_snapshot) is None:
        cmd = gen1.alloc_cmd()
        pt = gen1_session_poll_plaintext(mid, cmd)
        await gen1.send_and_wait(f"gen1 passive poll cmd=0x{cmd:02x}", pt)


async def async_run_gen1_stop_sequence(gen1: Gen1Session, mesh_device_id: int) -> None:
    """Stop sequence from ``gen1_pairingY-activity1`` captures."""
    mid = int(mesh_device_id)
    cmd = gen1.alloc_cmd()
    pt = gen1_status_probe_plaintext(mid, cmd)
    await gen1.send_and_wait(f"gen1 stop: status probe cmd=0x{cmd:02x}", pt)
    for repeat in (1, 2):
        pt = gen1_stop_plaintext(mid, cmd)
        await gen1.send_and_wait(f"gen1 stop cmd=0x{cmd:02x} ({repeat}/2)", pt)
    cmd2 = gen1.alloc_cmd()
    pt = gen1_stop_plaintext(mid, cmd2)
    await gen1.send_and_wait(f"gen1 stop cmd=0x{cmd2:02x}", pt)


async def async_run_gen1_ble_session(
    hass: HomeAssistant,
    params: Gen1BleSessionParams,
    session_runner: Callable[[Gen1Session, int], Awaitable[None]],
    *,
    listen_seconds: float = BLE_STATUS_LISTEN_S,
) -> dict[str, Any]:
    """Connect, subscribe, run gen1 application scripts, listen briefly, disconnect."""
    mid = int(params.mesh_device_id)
    transport = BhyveBleTransport(hass, params.address, params.network_key_16)
    magic = mesh_prefix_bytes(mid)
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
        await close_stale_connections_by_address(params.address)
        await transport.async_connect_and_subscribe(
            on_notify, tx_delay_ms=params.ble_profile.tx_delay_ms
        )
        gen1 = Gen1Session(magic=magic, send_plaintext=send_plaintext)
        await session_runner(gen1, mid)
        if listen_seconds > 0:
            await asyncio.sleep(listen_seconds)
        return gen1.status_snapshot
    except BhyveBleTransportError as e:
        _LOGGER.debug("[%s] gen1 session transport error: %s", params.address, e)
        raise Gen1RuntimeError(str(e)) from e
    finally:
        await transport.async_disconnect()
        _LOGGER.debug("[%s] gen1 session disconnected", params.address)


async def async_read_gen1_status(
    hass: HomeAssistant,
    params: Gen1BleSessionParams,
) -> dict[str, Any]:
    async def run_status(gen1: Gen1Session, mesh_id: int) -> None:
        await async_run_gen1_status_session(gen1, mesh_id)

    return await async_run_gen1_ble_session(hass, params, run_status)


async def async_gen1_manual_start(
    hass: HomeAssistant,
    params: Gen1BleSessionParams,
    duration_sec: int,
) -> dict[str, Any]:
    async def run_start(gen1: Gen1Session, mesh_id: int) -> None:
        await run_gen1_session(gen1, gen1_reconnect_write_plaintexts(mesh_id))
        cmd = gen1.alloc_cmd()
        pt = gen1_manual_start_plaintext(mesh_id, duration_sec, cmd=cmd)
        await gen1.send_and_wait(f"gen1 manual start {duration_sec}s cmd=0x{cmd:02x}", pt)

    return await async_run_gen1_ble_session(
        hass, params, run_start, listen_seconds=BLE_START_CONFIRM_LISTEN_S
    )


async def async_gen1_stop_watering(
    hass: HomeAssistant,
    params: Gen1BleSessionParams,
) -> dict[str, Any]:
    async def run_stop(gen1: Gen1Session, mesh_id: int) -> None:
        await async_run_gen1_status_session(gen1, mesh_id)
        await async_run_gen1_stop_sequence(gen1, mesh_id)

    return await async_run_gen1_ble_session(
        hass, params, run_stop, listen_seconds=BLE_COMMAND_LISTEN_S
    )


async def async_gen1_onboard_session(
    hass: HomeAssistant,
    params: Gen1BleSessionParams,
) -> dict[str, Any]:
    async def run_onboard(gen1: Gen1Session, mesh_id: int) -> None:
        await run_gen1_session(gen1, gen1_onboard_write_plaintexts(mesh_id))
        await async_run_gen1_status_session(gen1, mesh_id, passive_poll=False)

    return await async_run_gen1_ble_session(hass, params, run_onboard)
