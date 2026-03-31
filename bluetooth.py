from __future__ import annotations

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

from .const import (
    AES_CHAR_UUID,
    NETWORK_CHAR_UUID,
    READ_CHAR_UUID,
    WRITE_CHAR_UUID,
)


def is_bhyve_timer(service_info: BluetoothServiceInfoBleak) -> bool:
    """Best-effort filter for Orbit B-hyve timers.

    We currently filter by presence of known Orbit B-hyve GATT characteristic UUIDs
    in the advertisement data (if present). Many devices do not advertise
    characteristic UUIDs, so the config flow also allows manual selection.
    """
    uuids = {u.lower() for u in (service_info.service_uuids or [])}
    wanted = {
        NETWORK_CHAR_UUID,
        AES_CHAR_UUID,
        WRITE_CHAR_UUID,
        READ_CHAR_UUID,
    }
    return bool(uuids & wanted)

