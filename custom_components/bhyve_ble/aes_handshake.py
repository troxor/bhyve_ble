"""aes_char write/read handshake for GATT transport."""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from .provisioning import (
    AesHandshakeDerived,
    build_aes_char_write_payload,
    derive_from_aes_char_exchange,
)

_LOGGER = logging.getLogger(__name__)


class _AesCharClient(Protocol):
    async def write_gatt_char(
        self, char_specifier: str, data: bytes | bytearray, response: bool = False
    ) -> None: ...

    async def read_gatt_char(self, char_specifier: str, **kwargs: object) -> bytes | bytearray: ...


async def async_complete_aes_char_handshake(
    client: _AesCharClient,
    aes_char_uuid: str,
    *,
    tx_delay_ms: int = 0,
    max_attempts: int = 12,
) -> AesHandshakeDerived:
    """
    Write aes_char, poll read until the session material validates.

    Gen 1 uses ``tx_delay_ms=100``; Gen 2 uses ``0``.
    """
    write20 = build_aes_char_write_payload(tx_delay_ms)
    await client.write_gatt_char(aes_char_uuid, write20, response=True)

    last_error: ValueError | None = None
    for attempt in range(max_attempts):
        await asyncio.sleep(0.15 if attempt else 0.05)
        read20 = bytes(await client.read_gatt_char(aes_char_uuid))
        if len(read20) != 20:
            msg = f"aes_char read expected 20 bytes, got {len(read20)}"
            last_error = ValueError(msg)
            _LOGGER.debug("aes_char read wrong length (attempt %s)", attempt + 1)
            continue
        try:
            return derive_from_aes_char_exchange(write20, read20)
        except ValueError as e:
            last_error = e
            _LOGGER.debug("aes_char read not valid yet (attempt %s): %s", attempt + 1, e)

    msg = f"AES init response did not validate after retries: {last_error}"
    raise ValueError(msg)
