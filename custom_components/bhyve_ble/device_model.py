"""Device model display strings (no Home Assistant imports)."""

from __future__ import annotations

from .const import GENERATION_GEN1, GENERATION_GEN2
from .gen1_codec import GEN1_MODEL


def device_generation_label(generation: str | None) -> str | None:
    """Short generation tag for HA ``model_id`` (shown in parentheses by the UI)."""
    if generation == GENERATION_GEN1:
        return "Gen 1"
    if generation == GENERATION_GEN2:
        return "Gen 2"
    return None


def device_model_name(
    hw_version: str | None,
    *,
    generation: str | None,
) -> str:
    """Hardware model name only — generation goes in ``model_id`` for HA display."""
    if hw_version:
        return hw_version
    if generation == GENERATION_GEN1:
        return GEN1_MODEL
    return "Orbit B-hyve"
