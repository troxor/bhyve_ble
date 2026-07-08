"""Config-entry data helpers (no Home Assistant imports)."""

from __future__ import annotations

import base64
import binascii
from typing import Any

from .const import (
    CONF_DEVICE_GENERATION,
    CONF_DEVICE_ID,
    CONF_DEVICES,
    CONF_GEN1_RUN_PAIRING,
    CONF_GEN2_RUN_PAIRING,
    CONF_NETWORK_KEY_B64,
    CONF_NETWORK_KEY_INPUT,
    GENERATION_GEN2,
)
from .pybhyve.gen1_ops import gen1_network_key


def entry_has_network_key(data: dict[str, Any]) -> bool:
    """Return whether the integration entry already stores a 16-byte shared key."""
    b64 = data.get(CONF_NETWORK_KEY_B64)
    if not b64:
        return False
    try:
        return len(base64.b64decode(b64)) == 16
    except (TypeError, ValueError, binascii.Error):  # fmt: skip
        return False


def entry_has_gen2_device(data: dict[str, Any]) -> bool:
    """Return whether at least one onboarded timer is Gen 2."""
    devices = data.get(CONF_DEVICES) or {}
    for meta in devices.values():
        gen = (meta or {}).get(CONF_DEVICE_GENERATION, GENERATION_GEN2)
        if gen == GENERATION_GEN2:
            return True
    return False


def entry_needs_network_key_prompt(data: dict[str, Any] | None) -> bool:
    """Return whether to prompt for the shared Gen 2 key (no Gen 2 device and no stored key)."""
    if not data:
        return True
    return not entry_has_gen2_device(data) and not entry_has_network_key(data)


def entry_network_key_bytes(data: dict[str, Any] | None) -> bytes | None:
    """Return the integration entry shared key, or None if missing/invalid."""
    if not data:
        return None
    b64 = data.get(CONF_NETWORK_KEY_B64)
    if not b64:
        return None
    try:
        key = base64.b64decode(b64)
    except (TypeError, ValueError, binascii.Error):  # fmt: skip
        return None
    if len(key) != 16:
        return None
    return key


def entry_gen2_pairing_locked(data: dict[str, Any] | None) -> bool:
    """Return True when the integration already has Gen 2 device(s) and a shared network key."""
    if not data:
        return False
    return entry_has_gen2_device(data) and entry_network_key_bytes(data) is not None


def parse_gen2_credentials_submission(
    user_input: dict[str, Any],
    entry_data: dict[str, Any] | None,
) -> tuple[bytes | None, bool, dict[str, str]]:
    errors: dict[str, str] = {}
    locked = entry_gen2_pairing_locked(entry_data)
    run_pairing = True if locked else bool(user_input.get(CONF_GEN2_RUN_PAIRING, True))

    if locked:
        key = entry_network_key_bytes(entry_data)
        if key is None:
            errors["base"] = "cannot_connect"
        return key, True, errors

    key_input = user_input.get(CONF_NETWORK_KEY_INPUT, "")
    explicit_key: bytes | None = None
    if key_input and str(key_input).strip():
        try:
            explicit_key = gen1_network_key(str(key_input))
        except ValueError:
            errors["base"] = "invalid_key"

    return explicit_key, run_pairing, errors


def parse_gen1_credentials_submission(
    user_input: dict[str, Any],
) -> tuple[int | None, bytes | None, bool, dict[str, str]]:
    """Parse gen1 credentials step; third value is whether to run pairing/onboard."""
    errors: dict[str, str] = {}
    run_pairing = bool(user_input.get(CONF_GEN1_RUN_PAIRING, True))

    raw_device_id = user_input.get(CONF_DEVICE_ID, "").strip()
    device_id: int | None = None
    if not raw_device_id:
        device_id = None
    else:
        if str(raw_device_id).strip().lower().startswith("0x"):
            errors["base"] = "invalid_device_id"
        else:
            try:
                device_id = int(raw_device_id, 10)
            except (TypeError, ValueError):  # fmt: skip
                errors["base"] = "invalid_device_id"
        if device_id is not None and not 0 <= device_id <= 0xFFFF:
            errors["base"] = "invalid_device_id"

    device_key: bytes | None = None
    key_input = user_input.get(CONF_NETWORK_KEY_INPUT, "")
    if key_input and str(key_input).strip():
        try:
            device_key = gen1_network_key(str(key_input))
        except ValueError:
            errors["base"] = "invalid_key"

    if not run_pairing:
        if device_id is None or device_key is None:
            errors["base"] = "credentials_required"
    elif device_id is None and device_key is not None:
        errors["base"] = "invalid_device_id"

    return device_id, device_key, run_pairing, errors
