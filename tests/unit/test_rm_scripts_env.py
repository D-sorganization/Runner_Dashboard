"""Tests for RM subprocess environment sanitization (identity fix)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from coordination.rm_scripts import _rm_env, run_argv

pytestmark = pytest.mark.unit


def test_personal_gh_token_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "ghp_personal_token")
    monkeypatch.setenv("GITHUB_TOKEN", "github_pat_personal")

    env = _rm_env()

    assert "GH_TOKEN" not in env
    assert "GITHUB_TOKEN" not in env


def test_ghs_installation_token_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "ghs_installation_token_value")
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_other_installation")

    env = _rm_env()

    assert env["GH_TOKEN"] == "ghs_installation_token_value"
    assert env["GITHUB_TOKEN"] == "ghs_other_installation"


def test_fleet_gh_wrapper_prepended_when_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    wrapper_dir = tmp_path / "fleet-gh" / "bin"
    wrapper_dir.mkdir(parents=True)
    (wrapper_dir / "gh").write_text("")
    monkeypatch.setenv("FLEET_GH_WRAPPER_DIR", str(wrapper_dir))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    env = _rm_env()

    assert env["PATH"].startswith(str(wrapper_dir) + os.pathsep)
    assert env["FLEET_GH_APP"] == "general"


def test_path_untouched_when_wrapper_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    missing = Path("/nonexistent/fleet-gh-wrapper-dir")
    monkeypatch.setenv("FLEET_GH_WRAPPER_DIR", str(missing))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("FLEET_GH_APP", raising=False)

    env = _rm_env()

    assert env["PATH"] == "/usr/bin:/bin"
    assert "FLEET_GH_APP" not in env


def test_run_argv_passes_sanitized_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "ghp_should_not_leak")

    captured: dict = {}

    def fake_run(*_args, **kwargs):
        captured["env"] = kwargs.get("env")
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = ""
        mock.stderr = ""
        return mock

    with patch("coordination.rm_scripts.subprocess.run", side_effect=fake_run):
        run_argv(["true"], cwd=tmp_path)

    assert captured["env"] is not None
    assert "GH_TOKEN" not in captured["env"]
