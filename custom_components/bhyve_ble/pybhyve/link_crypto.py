"""Reference implementation of b-hyve BLE link-layer crypto + framing."""

from __future__ import annotations

import secrets
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

try:
    from Crypto.Cipher import AES
except ImportError as e:  # pragma: no cover
    raise ImportError("Install pycryptodome: uv sync (repo root)") from e

# Matches lib.ble.service.common / perform_crypto (bundle ~962789, ~964273)
ENCRYPTION_FRAME_SIZE = 16

# inc_ctr wraps at 4294967295 (bytecode refs ~964091-964099)
_CTR_MOD = 4294967295

# Gen1 NOTIFY bursts can lag tens of thousands of counter steps behind the client
# after missed frames, 0x00 per-write acks, or prior sessions (lab: skip≈26k on BH1G1).
GEN1_MAX_CTR_SKIP = 65535


def inc_ctr(n: int) -> int:
    return (int(n) + 1) % _CTR_MOD


def _aes_ecb_keystream_block(key: bytes, iv12: bytes, counter: int) -> bytes:
    if len(key) != 16:
        raise ValueError("AES key must be 16 bytes (decoded :network-key)")
    if len(iv12) < 12:
        raise ValueError("IV must be at least 12 bytes")
    block = bytearray(16)
    block[0:12] = iv12[:12]
    struct.pack_into("<I", block, 12, counter & 0xFFFFFFFF)
    return AES.new(key, AES.MODE_ECB).encrypt(bytes(block))


def perform_crypto(
    key: bytes,
    iv12: bytes,
    counter: int,
    data: bytes,
) -> tuple[bytes, int]:
    out = bytearray(len(data))
    ctr = int(counter)
    pos = 0
    while pos < len(data):
        ks = _aes_ecb_keystream_block(key, iv12, ctr)
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
    key16: bytes | None = None,
    iv12: bytes | None = None,
    enc_ctr: int | None = None,
    *,
    key: bytes | None = None,
) -> tuple[bytes, int]:
    k = key16 if key16 is not None else key
    if k is None or iv12 is None or enc_ctr is None:
        msg = "build_data_frame requires key16, iv12, and enc_ctr"
        raise TypeError(msg)
    if not 0 <= msg_type <= 255:
        raise ValueError("msg_type must be a byte")
    if len(plaintext) > 255:
        raise ValueError("plaintext length must fit in one byte (L ≤ 255)")
    L = len(plaintext)
    chk = checksum_16(msg_type, L, plaintext)
    if chk != (msg_type + L + sum(plaintext)):
        raise ValueError(
            "checksum overflow > 16 bits; shorten payload or match device wrapping rules"
        )

    ciphertext, new_ctr = perform_crypto(k, iv12, enc_ctr, plaintext)
    frame = bytearray()
    frame.append(msg_type)
    frame.append(L)
    frame.extend(ciphertext)
    frame.extend(struct.pack("<H", chk))
    return bytes(frame), new_ctr


def parse_data_frame(
    frame: bytes,
    key16: bytes | None = None,
    iv12: bytes | None = None,
    dec_ctr: int | None = None,
    *,
    key: bytes | None = None,
) -> tuple[int, bytes, int]:
    k = key16 if key16 is not None else key
    if k is None or iv12 is None or dec_ctr is None:
        msg = "parse_data_frame requires key16, iv12, and dec_ctr"
        raise TypeError(msg)
    if len(frame) < 4:
        raise ValueError("frame too short")
    T = frame[0]
    L = frame[1]
    end_body = 2 + L
    if len(frame) < end_body + 2:
        raise ValueError("frame truncated")
    C = frame[2:end_body]
    S = struct.unpack_from("<H", frame, end_body)[0]

    plaintext, new_ctr = perform_crypto(k, iv12, dec_ctr, C)
    if len(plaintext) != L:
        raise ValueError("length mismatch")

    calc = checksum_16(T, L, plaintext)
    if calc != S:
        raise ValueError(
            f"checksum mismatch: wire={S} calc={calc} (raw sum={T + L + sum(plaintext)})"
        )
    return T, plaintext, new_ctr


def parse_data_frame_resync(
    frame: bytes,
    *,
    key16: bytes,
    iv12: bytes,
    dec_ctr: int,
    expected_magic: bytes | None = None,
    max_ctr_skip: int = GEN1_MAX_CTR_SKIP,
    accept_plaintext: Callable[[bytes], bool] | None = None,
) -> tuple[int, bytes, int, int]:
    if max_ctr_skip < 0:
        msg = "max_ctr_skip must be >= 0"
        raise ValueError(msg)
    ctr = int(dec_ctr)
    last_err: ValueError | None = None
    for skip in range(max_ctr_skip + 1):
        try:
            msg_type, plaintext, new_ctr = parse_data_frame(
                frame,
                key16=key16,
                iv12=iv12,
                dec_ctr=ctr,
            )
        except ValueError as exc:
            last_err = exc
            ctr = inc_ctr(ctr)
            continue
        if accept_plaintext is not None and not accept_plaintext(plaintext):
            ctr = inc_ctr(ctr)
            continue
        if expected_magic is not None and (
            len(plaintext) < len(expected_magic)
            or plaintext[: len(expected_magic)] != expected_magic
        ):
            ctr = inc_ctr(ctr)
            continue
        return msg_type, plaintext, new_ctr, skip
    msg = "could not decrypt data frame (counter resync exhausted)"
    if last_err is not None:
        raise ValueError(msg) from last_err
    raise ValueError(msg)


def parse_inbound_data_frame(
    frame: bytes,
    *,
    key16: bytes,
    iv12: bytes,
    dec_ctr: int,
    expected_magic: bytes | None = None,
    max_ctr_skip: int = GEN1_MAX_CTR_SKIP,
    accept_plaintext: Callable[[bytes], bool] | None = None,
) -> tuple[int, bytes, int, int]:
    try:
        return parse_data_frame_resync(
            frame,
            key16=key16,
            iv12=iv12,
            dec_ctr=dec_ctr,
            expected_magic=expected_magic,
            max_ctr_skip=max_ctr_skip,
            accept_plaintext=accept_plaintext,
        )
    except ValueError:
        if expected_magic is None:
            raise
        return parse_data_frame_resync(
            frame,
            key16=key16,
            iv12=iv12,
            dec_ctr=dec_ctr,
            expected_magic=None,
            max_ctr_skip=max_ctr_skip,
            accept_plaintext=accept_plaintext,
        )


@dataclass(frozen=True, slots=True)
class SessionKeys:
    network_key16: bytes
    iv12: bytes
    enc_ctr: int
    dec_ctr: int


# --- network_char (0x0014): LE16(prefix) || 16-byte key (gen1 prefix = device id) ---


def build_network_char_payload(
    network_key_16: bytes,
    device_id: int | None = None,
) -> bytes:
    """
    network_char provision: LE16(prefix) || 16-byte key.

    Gen 2: prefix 1 (01 00). Gen 1: prefix MUST be the device-id used as
    the gen1 session magic on 0x81…0x86 — the network_char prefix must equal
    the per-frame magic (gen 1 never uses the 01 00 default). A prefix/magic mismatch
    makes the timer drop every command and never NOTIFY.
    """
    if len(network_key_16) != 16:
        raise ValueError("network key must be 16 raw bytes")
    if device_id is None:
        prefix = struct.pack("<H", 1)
    else:
        mid = int(device_id)
        if not 0 <= mid <= 0xFFFF:
            raise ValueError("device_id must be 0..65535")
        prefix = struct.pack("<H", mid)
    return prefix + network_key_16


# --- aes_char (0x000d): 20-byte write + composite from read (§5d) ---


@dataclass
class AesHandshakeDerived:
    iv12: bytes
    enc_ctr: int
    dec_ctr: int


def build_aes_char_write_payload(tx_delay_ms: int = 0) -> bytes:
    buf = bytearray(secrets.token_bytes(20))
    buf[11] = max(0, min(255, int(tx_delay_ms)))
    return bytes(buf)


def derive_from_aes_char_exchange(write20: bytes, read20: bytes) -> AesHandshakeDerived:
    """
    Derive session keys from the aes_char write/read exchange.

    Device read: first 4 bytes non-zero, bytes 4..19 zero.
    composite = read[0:4] + write[4:20]
    """
    if len(write20) != 20 or len(read20) != 20:
        raise ValueError("aes_char read/write must be 20 bytes")
    if read20[0:4] == b"\x00\x00\x00\x00":
        raise ValueError("invalid read: first 4 bytes all zero")
    if any(read20[i] != 0 for i in range(4, 20)):
        raise ValueError("invalid read: bytes 4..19 must be zero")

    composite = read20[0:4] + write20[4:20]
    iv12 = composite[0:12]
    enc_ctr = struct.unpack_from("<I", composite, 12)[0]
    dec_ctr = struct.unpack_from("<I", composite, 16)[0]
    return AesHandshakeDerived(iv12=iv12, enc_ctr=enc_ctr, dec_ctr=dec_ctr)


# --- tiny self-check ---

if __name__ == "__main__":  # pragma: no cover
    key = bytes(range(16))
    iv = bytes(range(12))
    enc = 0x11223344
    pt = b"hello b-hyve timer"
    frame, _ = build_data_frame(0x42, pt, key, iv, enc)
    # On the device, RX decrypts with dec_ctr from aes_char; this test uses the same counter to
    # verify framing + checksum + stream cipher only.
    T2, pt2, _ = parse_data_frame(frame, key, iv, enc)
    assert T2 == 0x42
    assert pt2 == pt

    # Stream XOR round-trip (counter advances per 16-byte chunk):
    ct, _ = perform_crypto(key, iv, enc, pt)
    pt3, _ = perform_crypto(key, iv, enc, ct)
    assert pt3 == pt

    prov = build_network_char_payload(key)
    assert len(prov) == 18
    assert prov[0:2] == b"\x01\x00"

    w = build_aes_char_write_payload(0)
    r = b"\xab\xcd\xef\x00" + b"\x00" * 16
    d = derive_from_aes_char_exchange(w, r)
    assert len(d.iv12) == 12

    print("bhyve_ble_link_crypto: self-check OK")
