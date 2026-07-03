"""Bundled pybhyve loads as a subpackage of bhyve_ble (HACS install layout)."""

from __future__ import annotations

import sys
from pathlib import Path


def test_pybhyve_imports_as_subpackage() -> None:
    integration = Path(__file__).resolve().parents[1] / "custom_components" / "bhyve_ble"
    assert (integration / "pybhyve" / "__init__.py").is_file()

    components = str(integration.parent)
    ble_root = str(integration)
    for path in (components, ble_root):
        while path in sys.path:
            sys.path.remove(path)

    for name in list(sys.modules):
        if name == "bhyve_ble" or name.startswith("bhyve_ble."):
            sys.modules.pop(name)

    sys.path.insert(0, components)

    from bhyve_ble.pybhyve.link_crypto import build_data_frame
    from bhyve_ble.pybhyve.gen2_codec import decode_gen2_ble_plaintext

    key16 = bytes(range(16))
    iv12 = bytes(range(12))
    frame, _ = build_data_frame(0x11, b"hi", key16=key16, iv12=iv12, enc_ctr=1)
    assert len(frame) >= 4
    assert decode_gen2_ble_plaintext.__module__.startswith("bhyve_ble.pybhyve")
