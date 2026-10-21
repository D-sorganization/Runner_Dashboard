"""Compare vendored runner_disk_guard_config with Repository_Management when present."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from shared_scripts import runner_disk_guard_config as vendored

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CANDIDATES = [
    parent / "Repository_Management" / "shared_scripts" / "runner_disk_guard_config.py"
    for parent in (_REPO_ROOT.parent, _REPO_ROOT.parent.parent)
]


def _load_upstream() -> ModuleType:
    path = next((p for p in _CANDIDATES if p.is_file()), None)
    if path is None:
        pytest.skip("no Repository_Management checkout beside this repository")
    spec = importlib.util.spec_from_file_location("rm_runner_disk_guard_config", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _upstream_repo_root() -> Path:
    path = next((p for p in _CANDIDATES if p.is_file()), None)
    if path is None:
        pytest.skip("no Repository_Management checkout beside this repository")
    return path.parents[1]


def test_vendored_schema_matches_upstream() -> None:
    upstream = _load_upstream()
    assert vendored.SCHEMA_ID == upstream.SCHEMA_ID
    cfg = vendored.load_runner_disk_guard_config(_REPO_ROOT / "config" / "runner_disk_guard.yaml")
    upstream_cfg = upstream.load_runner_disk_guard_config(_REPO_ROOT / "config" / "runner_disk_guard.yaml")
    assert cfg.thresholds == upstream_cfg.thresholds
    assert cfg.drain == upstream_cfg.drain
    assert cfg.cordon == upstream_cfg.cordon
    assert cfg.alerts == upstream_cfg.alerts


def test_vendored_files_match_upstream_bytes() -> None:
    upstream_root = _upstream_repo_root()
    pairs = (
        (
            _REPO_ROOT / "shared_scripts" / "runner_disk_guard_config.py",
            upstream_root / "shared_scripts" / "runner_disk_guard_config.py",
        ),
        (
            _REPO_ROOT / "config" / "runner_disk_guard.yaml",
            upstream_root / "config" / "runner_disk_guard.yaml",
        ),
    )
    for local, remote in pairs:
        assert local.read_bytes() == remote.read_bytes(), f"{local.name} drift from Repository_Management"
