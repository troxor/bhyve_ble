from __future__ import annotations

from bhyve_ble.const import CONF_MESH_DEVICE_ID, CONF_NETWORK_KEY_INPUT
from bhyve_ble.entry_data import parse_gen1_credentials_submission


def test_parse_gen1_credentials_mesh_only() -> None:
    mesh_id, device_key, errors = parse_gen1_credentials_submission(
        {CONF_MESH_DEVICE_ID: "1806", CONF_NETWORK_KEY_INPUT: ""}
    )
    assert errors == {}
    assert mesh_id == 1806
    assert device_key is None


def test_parse_gen1_credentials_hex_mesh_and_key() -> None:
    mesh_id, device_key, errors = parse_gen1_credentials_submission(
        {
            CONF_MESH_DEVICE_ID: "0x070e",
            CONF_NETWORK_KEY_INPUT: "0123456789abcdef0123456789abcdef",
        }
    )
    assert errors == {}
    assert mesh_id == 0x070E
    assert device_key == bytes.fromhex("0123456789abcdef0123456789abcdef")


def test_parse_gen1_credentials_empty_mesh() -> None:
    mesh_id, device_key, errors = parse_gen1_credentials_submission(
        {CONF_MESH_DEVICE_ID: "  ", CONF_NETWORK_KEY_INPUT: ""}
    )
    assert mesh_id is None
    assert device_key is None
    assert errors == {"base": "invalid_mesh_id"}


def test_parse_gen1_credentials_invalid_key() -> None:
    mesh_id, device_key, errors = parse_gen1_credentials_submission(
        {CONF_MESH_DEVICE_ID: "42", CONF_NETWORK_KEY_INPUT: "not-a-key"}
    )
    assert mesh_id == 42
    assert device_key is None
    assert errors == {"base": "invalid_key"}
