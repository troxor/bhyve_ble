"""Per-device BLE parameters by hose timer generation (fixed at onboarding; different hardware)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .const import CONF_DEVICE_GENERATION, GENERATION_GEN1, GENERATION_GEN2
from .pybhyve.constants import GEN1_TX_DELAY_MS, GEN2_TX_DELAY_MS
from .pybhyve.constants import GEN1_HANDLES, GEN2_HANDLES

GENERATION_CHOICES: Final[tuple[tuple[str, str], ...]] = (
    (GENERATION_GEN2, "Gen 2 (newer, e.g. HT25G2)"),
    (GENERATION_GEN1, "Gen 1 (older, e.g. BH1G1)"),
)


@dataclass(frozen=True, slots=True)
class DeviceBleProfile:
    """Link framing and AES init timing for one timer generation."""

    generation: str
    tx_delay_ms: int
    link_msg_type: int


_PROFILES: Final[dict[str, DeviceBleProfile]] = {
    GENERATION_GEN1: DeviceBleProfile(
        generation=GENERATION_GEN1,
        tx_delay_ms=GEN1_TX_DELAY_MS,
        link_msg_type=GEN1_HANDLES.link_msg_type,
    ),
    GENERATION_GEN2: DeviceBleProfile(
        generation=GENERATION_GEN2,
        tx_delay_ms=GEN2_TX_DELAY_MS,
        link_msg_type=GEN2_HANDLES.link_msg_type,
    ),
}


def device_ble_profile(generation: str | None) -> DeviceBleProfile:
    """Return BLE profile for a generation id; unknown values default to Gen 2."""
    if generation == GENERATION_GEN1:
        return _PROFILES[GENERATION_GEN1]
    return _PROFILES[GENERATION_GEN2]


def device_ble_profile_from_meta(meta: dict | None) -> DeviceBleProfile:
    """Load profile from per-address metadata under CONF_DEVICES."""
    if not meta:
        return _PROFILES[GENERATION_GEN2]
    return device_ble_profile(meta.get(CONF_DEVICE_GENERATION))
