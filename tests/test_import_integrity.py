"""Intra-package import integrity (catches missing constants, modules, etc.)."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1] / "custom_components" / "bhyve_ble"
_PYBHYVE_PKG = "pybhyve"


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


def _pybhyve_module_exists(relative: str) -> bool:
    base = _PKG / _PYBHYVE_PKG
    parts = relative.split(".")
    path = base.joinpath(*parts)
    return path.with_suffix(".py").is_file() or (path / "__init__.py").is_file()


def _ha_top_level_import_errors() -> list[str]:
    defined = {p.stem: _top_level_names(p) for p in _PKG.glob("*.py")}
    errors: list[str] = []
    for py in sorted(_PKG.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level != 1 or not node.module:
                continue
            parts = node.module.split(".")
            target = parts[0]
            if target == _PYBHYVE_PKG:
                if len(parts) > 1 and not _pybhyve_module_exists(".".join(parts[1:])):
                    errors.append(
                        f"{py.name}: missing pybhyve submodule .{'.'.join(parts[1:])}"
                    )
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    if len(parts) == 1 and not _pybhyve_module_exists(alias.name):
                        errors.append(
                            f"{py.name}: missing pybhyve submodule .{alias.name}"
                        )
                continue
            if target not in defined:
                errors.append(f"{py.name}: missing module .{target}")
                continue
            for alias in node.names:
                if alias.name != "*" and alias.name not in defined[target]:
                    errors.append(f"{py.name}: missing '{alias.name}' in .{target}")
    return errors


def _pybhyve_import_errors() -> list[str]:
    pybhyve = _PKG / _PYBHYVE_PKG
    py_mods = {p.stem for p in pybhyve.glob("*.py")}
    cli_mods = {p.stem for p in (pybhyve / "cli").glob("*.py")} if (pybhyve / "cli").is_dir() else set()
    errors: list[str] = []
    for py in sorted(pybhyve.rglob("*.py")):
        rel = py.relative_to(pybhyve)
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level < 1 or not node.module:
                continue
            first = node.module.split(".")[0]
            if node.level == 1:
                if rel.parts[0] == "cli":
                    if first not in cli_mods and first not in py_mods:
                        errors.append(f"pybhyve/{rel}: missing .{node.module}")
                elif first not in py_mods:
                    errors.append(f"pybhyve/{rel}: missing .{node.module}")
            elif node.level == 2 and first not in py_mods:
                errors.append(f"pybhyve/{rel}: missing ..{node.module}")
    return errors


def test_intra_package_imports_resolve() -> None:
    errors = _ha_top_level_import_errors() + _pybhyve_import_errors()
    assert not errors, "broken relative imports:\n" + "\n".join(errors)


def test_init_preloads_pybhyve_gen2_codec() -> None:
    """HA setup must preload protobuf via pybhyve (no top-level gen2_codec shim)."""
    src = (_PKG / "__init__.py").read_text(encoding="utf-8")
    assert "from .pybhyve import gen2_codec" in src
    assert "from . import gen2_codec" not in src
    assert not (_PKG / "gen2_codec.py").is_file()


def test_pure_modules_import() -> None:
    for name in (
        "entry_data",
        "device_profile",
        "device_credentials",
        "bluetooth",
    ):
        importlib.import_module(f"bhyve_ble.{name}")
