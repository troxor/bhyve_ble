"""Parse or generate the 16-byte BLE :network-key material."""

from __future__ import annotations

import base64
import binascii
import re
import secrets


def parse_or_generate_network_key(user_input: str | None) -> bytes:
    """Return 16 raw bytes from user input , or generate a random key if empty."""
    if user_input is None:
        return secrets.token_bytes(16)
    s = user_input.strip()
    if not s:
        return secrets.token_bytes(16)

    # 32 hex chars (with or without separators)
    hex_compact = re.sub(r"[\s:-]", "", s)
    if len(hex_compact) == 32 and all(c in "0123456789abcdefABCDEF" for c in hex_compact):
        return binascii.unhexlify(hex_compact)

    try:
        raw = base64.b64decode(s, validate=True)
    except binascii.Error as e:
        msg = "Invalid network key: use 32 hex characters or standard Base64"
        raise ValueError(msg) from e
    if len(raw) != 16:
        msg = f"Decoded key must be 16 bytes, got {len(raw)}"
        raise ValueError(msg)
    return raw
