from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from .constants import GEN2_STATUS_QUERY_DELAY_S, STATUS_QUERY_GAP_S
from .gen2_codec import (
    encode_get_battery_status_plaintext,
    encode_get_device_info_plaintext,
    encode_get_device_status_info_plaintext,
    encode_timer_mode_plaintext,
)

SendPlaintextFn = Callable[..., Awaitable[None]]


async def run_gen2_onboard_queries(send_plaintext: SendPlaintextFn) -> None:
    await send_plaintext(encode_get_device_info_plaintext(), label="getDeviceInfo")
    await send_plaintext(encode_get_device_status_info_plaintext(), label="getDeviceStatusInfo")


async def run_gen2_status_queries(send_plaintext: SendPlaintextFn) -> None:
    await send_plaintext(encode_get_device_status_info_plaintext(), label="getDeviceStatusInfo")
    await asyncio.sleep(GEN2_STATUS_QUERY_DELAY_S)
    await send_plaintext(encode_get_device_info_plaintext(), label="getDeviceInfo")
    await asyncio.sleep(STATUS_QUERY_GAP_S)
    await send_plaintext(encode_get_battery_status_plaintext(), label="getBatteryStatus")
    await asyncio.sleep(GEN2_STATUS_QUERY_DELAY_S)


async def run_gen2_manual_start(
    send_plaintext: SendPlaintextFn,
    duration_sec: int,
    *,
    station_id: int = 0,
) -> None:
    sec = int(duration_sec)
    sid = int(station_id)
    pt = encode_timer_mode_plaintext("manualMode", run_time_sec=sec, station_id=sid)
    await send_plaintext(pt, label=f"start manual watering {sec}s port {sid + 1}")


async def run_gen2_stop_watering(send_plaintext: SendPlaintextFn) -> None:
    await send_plaintext(encode_timer_mode_plaintext("offMode"), label="stop watering (offMode)")


__all__ = [
    "SendPlaintextFn",
    "run_gen2_manual_start",
    "run_gen2_onboard_queries",
    "run_gen2_status_queries",
    "run_gen2_stop_watering",
]
