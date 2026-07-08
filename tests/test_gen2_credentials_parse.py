from __future__ import annotations

import base64

from bhyve_ble.const import (
    CONF_DEVICE_GENERATION,
    CONF_DEVICES,
    CONF_GEN2_RUN_PAIRING,
    CONF_NETWORK_KEY_B64,
    CONF_NETWORK_KEY_INPUT,
    GENERATION_GEN2,
)
from bhyve_ble.entry_data import (
    entry_gen2_pairing_locked,
    parse_gen2_credentials_submission,
)

_HEX_KEY = "0123456789abcdef0123456789abcdef"


def test_entry_gen2_pairing_locked_requires_gen2_device_and_key() -> None:
    assert entry_gen2_pairing_locked(None) is False
    assert entry_gen2_pairing_locked({}) is False
    assert (
        entry_gen2_pairing_locked({CONF_NETWORK_KEY_B64: base64.b64encode(b"\x01" * 16).decode()})
        is False
    )
    assert (
        entry_gen2_pairing_locked(
            {
                CONF_DEVICES: {"AA:BB:CC:DD:EE:FF": {CONF_DEVICE_GENERATION: GENERATION_GEN2}},
            }
        )
        is False
    )
    assert (
        entry_gen2_pairing_locked(
            {
                CONF_NETWORK_KEY_B64: base64.b64encode(b"\x01" * 16).decode(),
                CONF_DEVICES: {"AA:BB:CC:DD:EE:FF": {CONF_DEVICE_GENERATION: GENERATION_GEN2}},
            }
        )
        is True
    )


def test_parse_gen2_pairing_default_generates_key() -> None:
    explicit_key, run_pairing, errors = parse_gen2_credentials_submission({}, None)
    assert errors == {}
    assert run_pairing is True
    assert explicit_key is None


def test_parse_gen2_verify_only_when_pairing_off() -> None:
    explicit_key, run_pairing, errors = parse_gen2_credentials_submission(
        {CONF_GEN2_RUN_PAIRING: False, CONF_NETWORK_KEY_INPUT: _HEX_KEY},
        {},
    )
    assert errors == {}
    assert run_pairing is False
    assert explicit_key is not None
    assert len(explicit_key) == 16


def test_parse_gen2_locked_forces_pairing_and_entry_key() -> None:
    entry_data = {
        CONF_NETWORK_KEY_B64: base64.b64encode(bytes.fromhex(_HEX_KEY)).decode(),
        CONF_DEVICES: {"AA:BB:CC:DD:EE:FF": {CONF_DEVICE_GENERATION: GENERATION_GEN2}},
    }
    explicit_key, run_pairing, errors = parse_gen2_credentials_submission(
        {CONF_GEN2_RUN_PAIRING: False},
        entry_data,
    )
    assert errors == {}
    assert run_pairing is True
    assert explicit_key == bytes.fromhex(_HEX_KEY)
