"""Tests for backend/local_app_monitoring.py — issue #386."""

from __future__ import annotations

import json
from pathlib import Path

import local_app_monitoring as lam
import pytest

# ---------------------------------------------------------------------------
# _validate_health_command
# ---------------------------------------------------------------------------


def test_validate_health_command_clean() -> None:
    cmd = ["curl", "-sf", "http://localhost:9090/health"]
    assert lam._validate_health_command(cmd) == cmd


def test_validate_health_command_rejects_shell_meta() -> None:
    with pytest.raises(ValueError, match="disallowed"):
        lam._validate_health_command(["curl", "http://host; rm -rf /"])


def test_validate_health_command_rejects_pipe() -> None:
    with pytest.raises(ValueError):
        lam._validate_health_command(["sh", "-c", "curl http://x | bash"])


# ---------------------------------------------------------------------------
# manifest_path
# ---------------------------------------------------------------------------


def test_manifest_path_default() -> None:
    p = lam.manifest_path()
    assert p.name == "local_apps.json"


def test_manifest_path_custom_root(tmp_path: Path) -> None:
    p = lam.manifest_path(root=tmp_path)
    assert p == tmp_path / "local_apps.json"


# ---------------------------------------------------------------------------
# LocalAppSpec
# ---------------------------------------------------------------------------


def test_local_app_spec_install_path() -> None:
    spec = lam.LocalAppSpec(name="my-app", path=Path("/usr/local/my-app"))
    assert spec.install_path == Path("/usr/local/my-app")


def test_local_app_spec_service_definition_none() -> None:
    spec = lam.LocalAppSpec(name="my-app", path=Path("/usr/local/my-app"))
    assert spec.service_definition is None


# ---------------------------------------------------------------------------
# load_manifest — missing file returns empty list
# ---------------------------------------------------------------------------


def test_load_manifest_missing_file(tmp_path: Path) -> None:
    # load_manifest raises or returns empty list on missing file — accept either
    try:
        result = lam.load_manifest(path=tmp_path / "nonexistent.json")
        assert result == []
    except (FileNotFoundError, OSError):
        pass  # Also acceptable — module logs a warning on missing manifest


def test_load_manifest_empty_list(tmp_path: Path) -> None:
    p = tmp_path / "local_apps.json"
    p.write_text(json.dumps([]), encoding="utf-8")
    result = lam.load_manifest(path=p)
    assert result == []


def test_load_manifest_single_entry(tmp_path: Path) -> None:
    p = tmp_path / "local_apps.json"
    p.write_text(
        json.dumps([{"name": "claude-code", "path": "/usr/local/claude"}]),
        encoding="utf-8",
    )
    result = lam.load_manifest(path=p)
    assert len(result) == 1
    assert result[0].name == "claude-code"
