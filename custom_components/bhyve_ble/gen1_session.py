"""Gen1 application session: auto-ACK notifies and scripted handshake steps."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .gen1_codec import (
    GEN1_ASYNC_NOTIFY_CMDS,
    GEN1_COMMIT_DRAIN_MAX_S,
    GEN1_COMMIT_QUIET_S,
    GEN1_POST_HANDSHAKE_CMD,
    GEN1_RESPONSE_BIT,
    decode_gen1_inner_payload,
    decode_gen1_plaintext,
    encode_gen1_ack,
    merge_gen1_status_record,
)

_LOGGER = logging.getLogger(__name__)

GEN1_STEP_DELAY_S = 0.10
GEN1_ACK_DELAY_S = 0.02
GEN1_RESPONSE_TIMEOUT_S = 8.0

SendPlaintextFn = Callable[[bytes, str], Awaitable[None]]


class Gen1Session:
    """Tracks pending request/response and auto-ACKs async gen1 notifies."""

    def __init__(
        self,
        *,
        magic: bytes,
        send_plaintext: SendPlaintextFn,
        step_delay_s: float = GEN1_STEP_DELAY_S,
        ack_delay_s: float = GEN1_ACK_DELAY_S,
        response_timeout_s: float = GEN1_RESPONSE_TIMEOUT_S,
    ) -> None:
        self._magic = magic
        self._send = send_plaintext
        self._step_delay_s = step_delay_s
        self._ack_delay_s = ack_delay_s
        self._response_timeout_s = response_timeout_s
        self._next_cmd = GEN1_POST_HANDSHAKE_CMD
        self._pending_req: int | None = None
        self._pending_fut: asyncio.Future[dict[str, Any]] | None = None
        self._ack_lock = asyncio.Lock()
        self._status: dict[str, dict[str, Any]] = {}
        self._last_notify_at: float | None = None
        self._ack_tasks: set[asyncio.Task[None]] = set()

    @property
    def status_snapshot(self) -> dict[str, dict[str, Any]]:
        return dict(self._status)

    def note_inner_payload(self, payload: bytes) -> dict[str, Any] | None:
        status = decode_gen1_inner_payload(payload)
        if status is None:
            return None
        merge_gen1_status_record(self._status, status)
        return status

    def alloc_cmd(self) -> int:
        cmd = self._next_cmd
        if cmd > 0xFF:
            msg = "gen1 cmd byte exhausted"
            raise ValueError(msg)
        self._next_cmd = cmd + 1
        return cmd

    def on_notify_plaintext(self, pt: bytes) -> None:
        self._last_notify_at = asyncio.get_running_loop().time()
        try:
            dec = decode_gen1_plaintext(pt)
        except ValueError:
            return
        try:
            self.note_inner_payload(bytes.fromhex(dec["payload_hex"]))
        except ValueError:
            pass
        cmd = int(dec["cmd"])
        if cmd in GEN1_ASYNC_NOTIFY_CMDS:
            if not (cmd & GEN1_RESPONSE_BIT):
                self._next_cmd = max(self._next_cmd, cmd + 1)
            task = asyncio.create_task(self._ack_notify(dec))
            self._ack_tasks.add(task)
            task.add_done_callback(self._ack_tasks.discard)
        req = dec.get("request_cmd")
        if (
            self._pending_req is not None
            and self._pending_fut is not None
            and not self._pending_fut.done()
            and req == self._pending_req
        ):
            self._pending_fut.set_result(dec)

    async def _ack_notify(self, dec: dict[str, Any]) -> None:
        async with self._ack_lock:
            ack = encode_gen1_ack(self._magic, int(dec["cmd"]), bytes.fromhex(dec["payload_hex"]))
            await self._send(ack, f"gen1 ACK {dec['cmd_hex']}")
            if self._ack_delay_s > 0:
                await asyncio.sleep(self._ack_delay_s)

    async def send_and_wait(self, label: str, pt: bytes) -> dict[str, Any] | None:
        req_cmd = pt[2]
        loop = asyncio.get_running_loop()
        self._pending_req = req_cmd
        self._pending_fut = loop.create_future()
        await self._send(pt, label)
        try:
            return await asyncio.wait_for(self._pending_fut, timeout=self._response_timeout_s)
        except TimeoutError:
            _LOGGER.debug(
                "gen1 no response for cmd 0x%02x within %.1fs (%s)",
                req_cmd,
                self._response_timeout_s,
                label,
            )
            return None
        finally:
            self._pending_req = None
            self._pending_fut = None
            if self._step_delay_s > 0:
                await asyncio.sleep(self._step_delay_s)

    async def send_only(self, label: str, pt: bytes) -> None:
        await self._send(pt, label)

    async def drain_commit_async(
        self,
        *,
        max_sec: float = GEN1_COMMIT_DRAIN_MAX_S,
        quiet_sec: float = GEN1_COMMIT_QUIET_S,
    ) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max_sec
        while loop.time() < deadline:
            last = self._last_notify_at
            if last is not None and (loop.time() - last) >= quiet_sec:
                return
            await asyncio.sleep(0.05)


async def run_gen1_session(
    gen1: Gen1Session,
    steps: list[tuple[str, bytes]],
    *,
    commit_cmd: int = 0x86,
) -> None:
    for label, pt in steps:
        req = pt[2]
        if req == commit_cmd:
            await gen1.send_only(label, pt)
            await gen1.drain_commit_async()
        else:
            await gen1.send_and_wait(label, pt)
