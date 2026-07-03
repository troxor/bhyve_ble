"""Smoke imports for pybhyve package."""

from pybhyve import __version__
from pybhyve.link_crypto import build_data_frame, parse_data_frame
from pybhyve.gen2_codec import (
    decode_gen2_ble_plaintext,
    encode_get_device_status_info_plaintext,
)


def test_version() -> None:
    assert __version__ == "0.1.0"


def test_gen2_status_request_decode() -> None:
    pt = encode_get_device_status_info_plaintext()
    dec = decode_gen2_ble_plaintext(pt)
    msg = dec.get("message") or {}
    assert "getDeviceStatusInfo" in msg


def test_link_crypto_roundtrip() -> None:
    key = bytes(range(16))
    iv = bytes(range(12))
    pt = b"\x01\x02hello-bhyve-ble!!"
    frame, enc = build_data_frame(0x11, pt, key, iv, 1)
    _t, out, dec = parse_data_frame(frame, key, iv, 1)
    assert out == pt
