from __future__ import annotations

from bhyve_ble.const import CONF_DEVICE_ID, CONF_GEN1_RUN_PAIRING, CONF_NETWORK_KEY_INPUT
from bhyve_ble.entry_data import parse_gen1_credentials_submission

_HEX_KEY = "0123456789abcdef0123456789abcdef"


def test_parse_pairing_default_generates_when_fields_empty() -> None:
    device_id, device_key, run_pairing, errors = parse_gen1_credentials_submission({})
    assert errors == {}
    assert run_pairing is True
    assert device_id is None
    assert device_key is None


def test_parse_verify_requires_both_credentials() -> None:
    _device_id, _device_key, _run_pairing, errors = parse_gen1_credentials_submission(
        {CONF_GEN1_RUN_PAIRING: False}
    )
    assert errors == {"base": "credentials_required"}

    _device_id, _device_key, _run_pairing, errors = parse_gen1_credentials_submission(
        {
            CONF_GEN1_RUN_PAIRING: False,
            CONF_DEVICE_ID: "33404",
            CONF_NETWORK_KEY_INPUT: "",
        }
    )
    assert errors == {"base": "credentials_required"}


def test_parse_verify_accepts_full_credentials() -> None:
    device_id, device_key, run_pairing, errors = parse_gen1_credentials_submission(
        {
            CONF_GEN1_RUN_PAIRING: False,
            CONF_DEVICE_ID: "33404",
            CONF_NETWORK_KEY_INPUT: _HEX_KEY,
        }
    )
    assert errors == {}
    assert run_pairing is False
    assert device_id == 33404
    assert device_key is not None
    assert len(device_key) == 16


def test_parse_pairing_rejects_key_without_device_id() -> None:
    _device_id, _device_key, _run_pairing, errors = parse_gen1_credentials_submission(
        {
            CONF_GEN1_RUN_PAIRING: True,
            CONF_NETWORK_KEY_INPUT: _HEX_KEY,
        }
    )
    assert errors == {"base": "invalid_device_id"}
