from __future__ import annotations

from bhyve_ble.orbit_codec import (
    decode_orbit_ble_plaintext,
    encode_get_device_status_info_plaintext,
)


def test_orbit_codec_roundtrip_decode_smoke() -> None:
    # We can't assert device semantics without fixtures, but we can ensure the
    # framing is self-consistent and decodes to the expected top-level structure.
    pt = encode_get_device_status_info_plaintext()
    decoded = decode_orbit_ble_plaintext(pt)
    assert "_framing" in decoded
    assert "message" in decoded
