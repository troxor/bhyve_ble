from __future__ import annotations

from bhyve_ble.link_crypto import build_data_frame, parse_data_frame


def test_link_crypto_roundtrip() -> None:
    key16 = bytes(range(16))
    iv12 = bytes(range(12))
    enc_ctr = 0x11223344
    dec_ctr = 0x11223344
    pt = b"hello Orbit B-hyve timer"
    frame, enc2 = build_data_frame(0x11, pt, key16=key16, iv12=iv12, enc_ctr=enc_ctr)
    t, pt2, dec2 = parse_data_frame(frame, key16=key16, iv12=iv12, dec_ctr=dec_ctr)
    assert t == 0x11
    assert pt2 == pt
    assert enc2 != enc_ctr
    assert dec2 != dec_ctr

