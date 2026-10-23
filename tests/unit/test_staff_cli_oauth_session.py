"""OAuth session probes for staff CLI providers (#2170)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import patch

import pytest
from staff.cli_oauth_session import (
    _has_refresh_token,
    antigravity_oauth_session_alive,
    claude_oauth_session_alive,
    invalidate_oauth_probe_cache,
    oauth_expired_in_file,
    oauth_has_refresh_token_for_provider,
    oauth_last_refresh_epoch_for_provider,
    provider_oauth_session_alive,
    resolve_claude_config_dir,
)

pytestmark = pytest.mark.unit


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


@pytest.mark.parametrize("signed_in", [True, False])
def test_antigravity_145_probes_authenticated_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signed_in: bool
) -> None:
    """Replay native CLI 1.4.5: auth status is invalid even after Google sign-in."""
    home = tmp_path / ".gemini"
    home.mkdir()
    (home / "settings.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(home))

    def fake_run(cmd: list[str], **kwargs: object) -> CompletedProcess[str]:
        if cmd[1:] == ["auth", "status"]:
            return CompletedProcess(
                cmd, 1, "", "Prompts are read only from -p/--print, -i/--prompt-interactive, or stdin"
            )
        assert cmd == ["/usr/bin/agy", "models"]
        assert kwargs["capture_output"] is True
        assert kwargs["timeout"] == 30.0
        return CompletedProcess(cmd, 0, "gemini-3.8-flash-high" if signed_in else "Not signed in", "")

    invalidate_oauth_probe_cache()
    with patch("staff.cli_oauth_session.subprocess.run", fake_run):
        alive, _detail = antigravity_oauth_session_alive("/usr/bin/agy")
    assert alive is signed_in


CLAUDE_AUTH_EXPIRED_OUTPUT = (
    "Authentication expired. Please run CLAUDE_CONFIG_DIR=~/.config/runner-dashboard/claude claude auth login\n"
)


def test_antigravity_probe_treats_auth_timeout_as_expired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / ".gemini"
    home.mkdir()
    (home / "settings.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(home))

    def _fake_run(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(args=["agy", "models"], returncode=1, stdout="", stderr=AGY_AUTH_TIMEOUT_OUTPUT)

    invalidate_oauth_probe_cache()
    with patch("staff.cli_oauth_session.subprocess.run", _fake_run):
        alive, detail = antigravity_oauth_session_alive("/usr/bin/agy")
    assert alive is False
    assert "authentication timed out" in detail.lower()


def test_antigravity_cold_probe_has_time_to_list_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Replay post-deploy cold startup exceeding the generic eight-second deadline."""
    home = tmp_path / ".gemini"
    home.mkdir()
    (home / "settings.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(home))

    def cold_start(cmd: list[str], **kwargs: object) -> CompletedProcess[str]:
        if kwargs["timeout"] == 8.0:
            raise TimeoutExpired(cmd, 8.0)
        assert kwargs["timeout"] == 30.0
        return CompletedProcess(cmd, 0, "gemini-3.8-flash-high", "")

    invalidate_oauth_probe_cache()
    with patch("staff.cli_oauth_session.subprocess.run", cold_start):
        assert antigravity_oauth_session_alive("/usr/bin/agy") == (True, "OAuth session active")


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


def test_claude_setup_token_env_counts_as_alive_without_credentials_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DeskComputer durable seat: CLAUDE_CODE_OAUTH_TOKEN only (no .credentials.json)."""
    cfg = tmp_path / "empty-claude-seat"
    cfg.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "setup-token-seat-secret")
    assert not (cfg / ".credentials.json").is_file()

    def _probe_must_not_run(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        raise AssertionError("subprocess must not run when setup-token env is configured")

    invalidate_oauth_probe_cache()
    with patch("staff.cli_oauth_session.subprocess.run", _probe_must_not_run):
        alive, detail = claude_oauth_session_alive("/usr/bin/claude")
        via_provider, provider_detail = provider_oauth_session_alive("claude")

    assert alive is True
    assert via_provider is True
    assert "setup-token-seat-secret" not in detail
    assert "setup-token-seat-secret" not in provider_detail
    assert "setup-token" in detail.lower() or "oauth" in detail.lower()


def test_claude_expired_with_refresh_token_refreshes_and_syncs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    seat = tmp_path / ".config" / "runner-dashboard" / "claude"
    home_claude = tmp_path / ".claude"
    seat.mkdir(parents=True)
    home_claude.mkdir(parents=True)
    seat_cred = seat / ".credentials.json"
    home_cred = home_claude / ".credentials.json"
    expired_ms = int((time.time() - 600) * 1000)
    fresh_ms = int((time.time() + 8 * 3600) * 1000)
    seat_cred.write_text(
        json.dumps({"claudeAiOauth": {"expiresAt": expired_ms, "refreshToken": "rt-valid"}}),
        encoding="utf-8",
    )
    assert _has_refresh_token(seat_cred) is True
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> CompletedProcess[str]:
        calls.append(cmd)
        if cmd[1:] == ["-p", "ok", "--max-turns", "1"]:
            assert kwargs.get("timeout") == 25.0
            seat_cred.write_text(
                json.dumps({"claudeAiOauth": {"expiresAt": fresh_ms, "refreshToken": "rt-valid"}}),
                encoding="utf-8",
            )
            return CompletedProcess(cmd, 0, "ok", "")
        return CompletedProcess(cmd, 0, "authenticated", "")

    invalidate_oauth_probe_cache()
    with (
        patch("staff.cli_oauth_session.Path.home", return_value=tmp_path),
        patch("staff.cli_oauth_session.subprocess.run", fake_run),
    ):
        assert oauth_has_refresh_token_for_provider("claude") is True
        alive, _detail = claude_oauth_session_alive("/usr/bin/claude")
    assert alive is True
    assert ["/usr/bin/claude", "-p", "ok", "--max-turns", "1"] in calls
    assert home_cred.is_file()
    assert (home_cred.stat().st_mode & 0o777) == 0o600


def test_antigravity_jetski_state_overrides_stale_oauth_creds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    home = tmp_path / ".gemini"
    agy_dir = home / "antigravity-cli"
    agy_dir.mkdir(parents=True)
    oauth_file = home / "oauth_creds.json"
    oauth_file.write_text(json.dumps({"expiresAt": int((time.time() - 86400) * 1000)}), encoding="utf-8")
    old_ts = time.time() - 131 * 86400
    os.utime(oauth_file, (old_ts, old_ts))
    jetski = agy_dir / "jetski_state.pbtxt"
    jetski.write_text("user_tier: true\n", encoding="utf-8")
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(home))

    invalidate_oauth_probe_cache()
    with patch(
        "staff.cli_oauth_session.subprocess.run",
        return_value=CompletedProcess(["/usr/bin/agy", "models"], 0, "gemini-3.8-flash-high", ""),
    ):
        alive, _detail = antigravity_oauth_session_alive("/usr/bin/agy")
    assert alive is True
    refreshed = oauth_last_refresh_epoch_for_provider("antigravity")
    assert refreshed is not None and (time.time() - refreshed) < 60
