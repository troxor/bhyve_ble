"""Counter resync for missed NOTIFY frames."""

from __future__ import annotations

from pybhyve.link_crypto import (
    GEN1_MAX_CTR_SKIP,
    build_data_frame,
    inc_ctr,
    parse_data_frame_resync,
)

KEY = bytes(range(16))
IV = bytes(range(12))


def test_parse_data_frame_resync_at_current_counter() -> None:
    pt = b"\x08\x80\x81\x05"
    dec = 10
    frame, new_dec = build_data_frame(0x10, pt, key16=KEY, iv12=IV, enc_ctr=dec)
    _, out, actual_dec, skip = parse_data_frame_resync(
        frame,
        key16=KEY,
        iv12=IV,
        dec_ctr=dec,
        expected_magic=b"\x08\x80",
    )
    assert skip == 0
    assert out == pt
    assert actual_dec == new_dec


def test_parse_data_frame_resync_skips_one_missed_step() -> None:
    pt = b"\x08\x80\xc1\x00"
    dec = 10
    # Device encrypted after one prior NOTIFY the client never saw.
    frame, new_dec = build_data_frame(0x10, pt, key16=KEY, iv12=IV, enc_ctr=inc_ctr(dec))
    _, out, actual_dec, skip = parse_data_frame_resync(
        frame,
        key16=KEY,
        iv12=IV,
        dec_ctr=dec,
        expected_magic=b"\x08\x80",
        max_ctr_skip=4,
    )
    assert skip == 1
    assert out == pt
    assert actual_dec == new_dec


def test_gen1_max_ctr_skip_is_large_enough_for_bluez_drift() -> None:
    assert GEN1_MAX_CTR_SKIP >= 65535
