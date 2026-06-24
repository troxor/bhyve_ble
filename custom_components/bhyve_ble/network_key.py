"""Parse or generate the 16-byte BLE :network-key material."""

from __future__ import annotations

import base64
import binascii
import secrets

_HEX_CHARS = frozenset("0123456789abcdefABCDEF")


def parse_network_key(user_input: str) -> bytes:
    """
    Parse a user-supplied 16-byte network key.

    Accepts standard Base64 (as shown in the Orbit web UI) or exactly 32 hex characters.
    """
    s = user_input.strip()
    if not s:
        msg = "network key cannot be empty"
        raise ValueError(msg)

    if len(s) == 32 and all(c in _HEX_CHARS for c in s):
        return binascii.unhexlify(s)

    try:
        raw = base64.b64decode(s, validate=True)
    except binascii.Error as e:
        msg = "Invalid network key: use Base64 from the Orbit app or 32 hex characters"
        raise ValueError(msg) from e
    if len(raw) != 16:
        msg = f"Decoded key must be 16 bytes, got {len(raw)}"
        raise ValueError(msg)
    return raw


def parse_or_generate_network_key(user_input: str | None) -> bytes:
    """Return 16 raw bytes from user input, or generate a random key if empty."""
    if user_input is None:
        return secrets.token_bytes(16)
    s = user_input.strip()
    if not s:
        return secrets.token_bytes(16)
    return parse_network_key(s)
