"""Config-entry data helpers (no Home Assistant imports)."""

from __future__ import annotations

import base64
import binascii
from typing import Any

from .const import (
    CONF_DEVICE_GENERATION,
    CONF_DEVICES,
    CONF_MESH_DEVICE_ID,
    CONF_NETWORK_KEY_B64,
    CONF_NETWORK_KEY_INPUT,
    GENERATION_GEN1,
    GENERATION_GEN2,
)
from .network_key import parse_or_generate_network_key


def entry_has_network_key(data: dict[str, Any]) -> bool:
    """Return whether the integration entry already stores a 16-byte shared key."""
    b64 = data.get(CONF_NETWORK_KEY_B64)
    if not b64:
        return False
    try:
        return len(base64.b64decode(b64)) == 16
    except (TypeError, ValueError, binascii.Error):
        return False


def entry_has_gen2_device(data: dict[str, Any]) -> bool:
    """Return whether at least one onboarded timer is Gen 2 (or legacy without generation)."""
    devices = data.get(CONF_DEVICES) or {}
    for meta in devices.values():
        gen = (meta or {}).get(CONF_DEVICE_GENERATION, GENERATION_GEN2)
        if gen != GENERATION_GEN1:
            return True
    return False


def entry_needs_network_key_prompt(data: dict[str, Any] | None) -> bool:
    """Return whether to prompt for the shared Gen 2 key (no Gen 2 device and no stored key)."""
    if not data:
        return True
    return not entry_has_gen2_device(data) and not entry_has_network_key(data)


def parse_gen1_credentials_submission(
    user_input: dict[str, Any],
) -> tuple[int | None, bytes | None, dict[str, str]]:
    """Parse mesh id and optional per-device key from the gen1 credentials step."""
    errors: dict[str, str] = {}
    raw_mesh = user_input.get(CONF_MESH_DEVICE_ID, "").strip()
    mesh_id: int | None = None
    if not raw_mesh:
        errors["base"] = "invalid_mesh_id"
    else:
        try:
            mesh_id = int(raw_mesh, 0) if raw_mesh.lower().startswith("0x") else int(raw_mesh, 10)
        except (TypeError, ValueError):
            errors["base"] = "invalid_mesh_id"
        if mesh_id is not None and not 0 <= mesh_id <= 0xFFFF:
            errors["base"] = "invalid_mesh_id"

    device_key: bytes | None = None
    key_input = user_input.get(CONF_NETWORK_KEY_INPUT, "")
    if key_input and str(key_input).strip():
        try:
            device_key = parse_or_generate_network_key(str(key_input))
        except ValueError:
            errors["base"] = "invalid_key"

    return mesh_id, device_key, errors
