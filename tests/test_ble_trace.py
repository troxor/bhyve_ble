"""BLE ATT trace formatting."""

from pybhyve.ble_trace import format_att_trace_line, network_char_detail


def test_format_att_trace_write_f_with_plaintext() -> None:
    lines = format_att_trace_line(
        "44:67:55:17:d8:4c",
        "write_f",
        "write_char",
        "0x000b",
        bytes.fromhex("aabbcc"),
        plaintext=bytes.fromhex("0680810540"),
        detail="gen1 write 0x81",
    )
    assert len(lines) == 2
    assert "write_f" in lines[0]
    assert "write_char" in lines[0]
    assert "@0x000b" in lines[0]
    assert "hex: aabbcc" in lines[0]
    assert "pt=0680810540" in lines[1]
    assert "gen1 write 0x81" in lines[1]


def test_network_char_detail() -> None:
    wire = bytes.fromhex("0680") + bytes(16)
    detail = network_char_detail(wire)
    assert "prefix=0680 (device_id 32774)" in detail
    assert "key=<redacted>" in detail
    assert "key=" not in detail.replace("key=<redacted>", "")


def test_network_char_wire_hex_for_trace_redacts_key() -> None:
    from pybhyve.ble_trace import network_char_wire_hex_for_trace

    wire = bytes.fromhex("0680") + bytes.fromhex("ab" * 16)
    assert network_char_wire_hex_for_trace(wire) == "0680<key redacted>"
