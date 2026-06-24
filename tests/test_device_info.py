from __future__ import annotations

from bhyve_ble.const import GENERATION_GEN1, GENERATION_GEN2
from bhyve_ble.device_model import device_generation_label, device_model_name


def test_device_model_name_gen2_hw() -> None:
    assert device_model_name("HT25G2-0001", generation=GENERATION_GEN2) == "HT25G2-0001"


def test_device_generation_label() -> None:
    assert device_generation_label(GENERATION_GEN2) == "Gen 2"
    assert device_generation_label(GENERATION_GEN1) == "Gen 1"


def test_device_model_name_gen1_default() -> None:
    assert device_model_name(None, generation=GENERATION_GEN1) == "HT25"


def test_device_model_name_gen2_default() -> None:
    assert device_model_name(None, generation=GENERATION_GEN2) == "Orbit B-hyve"
