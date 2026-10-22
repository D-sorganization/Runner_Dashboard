"""OAuth session probes for staff CLI providers (#2170)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest
from staff.cli_oauth_session import (
    antigravity_oauth_session_alive,
    claude_oauth_session_alive,
    invalidate_oauth_probe_cache,
    oauth_expired_in_file,
    provider_oauth_session_alive,
    resolve_claude_config_dir,
)


def test_oauth_expired_in_file_detects_past_expiry(tmp_path: Path) -> None:
    cred = tmp_path / "oauth_creds.json"
    cred.write_text(json.dumps({"expiresAt": int((time.time() - 3600) * 1000)}), encoding="utf-8")
    assert oauth_expired_in_file(cred) is True


def test_oauth_expired_in_file_false_when_future(tmp_path: Path) -> None:
    cred = tmp_path / "oauth_creds.json"
    cred.write_text(json.dumps({"expiresAt": int((time.time() + 3600) * 1000)}), encoding="utf-8")
    assert oauth_expired_in_file(cred) is False


def test_resolve_claude_config_dir_prefers_runner_dashboard_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    seat = tmp_path / ".config" / "runner-dashboard" / "claude"
    seat.mkdir(parents=True)
    (seat / ".credentials.json").write_text("{}", encoding="utf-8")
    with patch("staff.cli_oauth_session.Path.home", return_value=tmp_path):
        assert resolve_claude_config_dir() == seat


AGY_AUTH_TIMEOUT_OUTPUT = (
    "Authentication required. Please visit the URL to log in... "
    "Waiting for authentication (timeout 60s)\n"
    "Error: authentication timed out\n"
)

CLAUDE_AUTH_EXPIRED_OUTPUT = (
    "Authentication expired. Please run CLAUDE_CONFIG_DIR=~/.config/runner-dashboard/claude claude auth login\n"
)


def test_antigravity_probe_treats_auth_timeout_as_expired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / ".gemini"
    home.mkdir()
    (home / "settings.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(home))

    def _fake_run(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(args=["agy", "auth", "status"], returncode=1, stdout="", stderr=AGY_AUTH_TIMEOUT_OUTPUT)

    invalidate_oauth_probe_cache()
    with patch("staff.cli_oauth_session.subprocess.run", _fake_run):
        alive, detail = antigravity_oauth_session_alive("/usr/bin/agy")
    assert alive is False
    assert "authentication timed out" in detail.lower()


def test_claude_probe_treats_auth_expired_message_as_dead(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "claude"
    cfg.mkdir()
    (cfg / ".credentials.json").write_text(
        json.dumps({"expiresAt": int((time.time() + 7200) * 1000)}), encoding="utf-8"
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))

    def _fake_run(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(
            args=["claude", "auth", "status"], returncode=1, stdout="", stderr=CLAUDE_AUTH_EXPIRED_OUTPUT
        )

    invalidate_oauth_probe_cache()
    with patch("staff.cli_oauth_session.subprocess.run", _fake_run):
        alive, _detail = claude_oauth_session_alive("/usr/bin/claude")
    assert alive is False


def test_provider_oauth_session_alive_unknown_provider_is_ok() -> None:
    assert provider_oauth_session_alive("ollama") == (True, "")
