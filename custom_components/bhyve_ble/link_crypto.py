from __future__ import annotations

import struct
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

ENCRYPTION_FRAME_SIZE = 16
_CTR_MOD = 4294967295  # (n + 1) % 4294967295


def inc_ctr(n: int) -> int:
    return (int(n) + 1) % _CTR_MOD


def _aes_ecb_encrypt_block(key16: bytes, block16: bytes) -> bytes:
    if len(key16) != 16:
        msg = "AES key must be 16 bytes"
        raise ValueError(msg)
    if len(block16) != 16:
        msg = "ECB block must be 16 bytes"
        raise ValueError(msg)
    # Orbit wire format uses single-block AES-ECB for the network key step (not bulk data).
    cipher = Cipher(algorithms.AES(key16), modes.ECB())  # noqa: S305
    enc = cipher.encryptor()
    return enc.update(block16) + enc.finalize()


def _keystream_block(key16: bytes, iv12: bytes, counter: int) -> bytes:
    if len(iv12) < 12:
        msg = "IV must be at least 12 bytes"
        raise ValueError(msg)
    block = bytearray(16)
    block[0:12] = iv12[:12]
    struct.pack_into("<I", block, 12, counter & 0xFFFFFFFF)
    return _aes_ecb_encrypt_block(key16, bytes(block))


def perform_crypto(key16: bytes, iv12: bytes, counter: int, data: bytes) -> tuple[bytes, int]:
    """XOR stream cipher used by the device/app for both encrypt and decrypt."""
    out = bytearray(len(data))
    ctr = int(counter)
    pos = 0
    while pos < len(data):
        ks = _keystream_block(key16, iv12, ctr)
        n = min(ENCRYPTION_FRAME_SIZE, len(data) - pos)
        for i in range(n):
            out[pos + i] = data[pos + i] ^ ks[i]
        pos += n
        ctr = inc_ctr(ctr)
    return bytes(out), ctr


def checksum_16(msg_type: int, body_len: int, plaintext: bytes) -> int:
    s = (msg_type & 0xFF) + (body_len & 0xFF) + sum(plaintext)
    return s & 0xFFFF


def build_data_frame(
    msg_type: int,
    plaintext: bytes,
    *,
    key16: bytes,
    iv12: bytes,
    enc_ctr: int,
) -> tuple[bytes, int]:
    """Outbound frame: [T][L][ciphertext][checksum u16le]."""
    if not 0 <= msg_type <= 255:
        msg = "msg_type must fit in a byte"
        raise ValueError(msg)
    if len(plaintext) > 255:
        msg = "plaintext length must fit in one byte (<= 255)"
        raise ValueError(msg)
    L = len(plaintext)
    chk = checksum_16(msg_type, L, plaintext)
    ciphertext, new_ctr = perform_crypto(key16, iv12, enc_ctr, plaintext)
    frame = bytearray()
    frame.append(msg_type)
    frame.append(L)
    frame.extend(ciphertext)
    frame.extend(struct.pack("<H", chk))
    return bytes(frame), new_ctr


def parse_data_frame(
    frame: bytes,
    *,
    key16: bytes,
    iv12: bytes,
    dec_ctr: int,
) -> tuple[int, bytes, int]:
    """Inbound frame: verify checksum then decrypt."""
    if len(frame) < 4:
        msg = "frame too short"
        raise ValueError(msg)
    T = frame[0]
    L = frame[1]
    end_body = 2 + L
    if len(frame) < end_body + 2:
        msg = "frame truncated"
        raise ValueError(msg)
    C = frame[2:end_body]
    S = struct.unpack_from("<H", frame, end_body)[0]

    plaintext, new_ctr = perform_crypto(key16, iv12, dec_ctr, C)
    if len(plaintext) != L:
        msg = "length mismatch"
        raise ValueError(msg)
    calc = checksum_16(T, L, plaintext)
    if calc != S:
        msg = f"checksum mismatch: wire={S} calc={calc}"
        raise ValueError(msg)
    return T, plaintext, new_ctr


@dataclass(frozen=True, slots=True)
class SessionKeys:
    network_key16: bytes
    iv12: bytes
    enc_ctr: int
    dec_ctr: int
