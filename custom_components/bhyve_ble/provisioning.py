from __future__ import annotations

import secrets
import struct
from dataclasses import dataclass


def build_network_char_payload(network_key_16: bytes) -> bytes:
    """network_char: LE16(1) || 16-byte network key."""
    if len(network_key_16) != 16:
        msg = "network key must be 16 raw bytes"
        raise ValueError(msg)
    return struct.pack("<H", 1) + network_key_16


def build_aes_char_write_payload(tx_delay_ms: int = 0) -> bytes:
    """aes_char write: 20 bytes, with byte[11] overwritten by tx_delay."""
    buf = bytearray(secrets.token_bytes(20))
    buf[11] = max(0, min(255, int(tx_delay_ms)))
    return bytes(buf)


@dataclass(frozen=True, slots=True)
class AesHandshakeDerived:
    iv12: bytes
    enc_ctr: int
    dec_ctr: int


def derive_from_aes_char_exchange(write20: bytes, read20: bytes) -> AesHandshakeDerived:
    """Derive iv/ctrs from the aes_char write+read exchange."""
    if len(write20) != 20 or len(read20) != 20:
        msg = "aes_char read/write must be 20 bytes"
        raise ValueError(msg)
    if read20[0:4] == b"\x00\x00\x00\x00":
        msg = "invalid aes_char read: first 4 bytes all zero"
        raise ValueError(msg)
    if any(read20[i] != 0 for i in range(4, 20)):
        msg = "invalid aes_char read: bytes 4..19 must be zero"
        raise ValueError(msg)

    composite = read20[0:4] + write20[4:20]
    iv12 = composite[0:12]
    enc_ctr = struct.unpack_from("<I", composite, 12)[0]
    dec_ctr = struct.unpack_from("<I", composite, 16)[0]
    return AesHandshakeDerived(iv12=iv12, enc_ctr=enc_ctr, dec_ctr=dec_ctr)
