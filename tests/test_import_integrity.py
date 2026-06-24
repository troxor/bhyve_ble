"""Intra-package import integrity (catches missing gen1_codec constants, etc.)."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1] / "custom_components" / "bhyve_ble"


def _top_level_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _intra_package_import_errors() -> list[str]:
    defined = {
        p.stem: _top_level_names(p)
        for p in _PKG.glob("*.py")
        if p.name != "orbit_pb_api_pb2.py"
    }
    errors: list[str] = []
    for py in sorted(_PKG.glob("*.py")):
        if py.name == "orbit_pb_api_pb2.py":
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level != 1 or not node.module:
                continue
            target = node.module.split(".")[0]
            if target == "orbit_pb_api_pb2":
                continue
            if target not in defined:
                errors.append(f"{py.name}: missing module .{target}")
                continue
            for alias in node.names:
                if alias.name != "*" and alias.name not in defined[target]:
                    errors.append(f"{py.name}: missing '{alias.name}' in .{target}")
    return errors


def test_intra_package_imports_resolve() -> None:
    errors = _intra_package_import_errors()
    assert not errors, "broken relative imports:\n" + "\n".join(errors)


def test_gen1_session_import_chain() -> None:
    """Regression: gen1_session must import all symbols from gen1_codec."""
    import bhyve_ble.gen1_codec
    import bhyve_ble.gen1_session  # noqa: F401


def test_pure_modules_import() -> None:
    for name in (
        "entry_data",
        "network_key",
        "link_crypto",
        "provisioning",
        "gen1_codec",
        "gen1_session",
        "device_profile",
        "device_credentials",
        "bluetooth",
    ):
        importlib.import_module(f"bhyve_ble.{name}")
