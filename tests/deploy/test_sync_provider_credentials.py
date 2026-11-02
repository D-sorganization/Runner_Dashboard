"""Tests for bi-directional Windows <-> WSL provider credential mirroring."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "sync_provider_credentials.py"
_SPEC = importlib.util.spec_from_file_location("sync_provider_credentials", _SCRIPT)
assert _SPEC and _SPEC.loader
sync_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sync_mod)


def _assert_owner_only(path: Path) -> None:
    # Windows has no POSIX permission bits (files report 0o666), so only check 0600 on POSIX.
    if os.name == "posix":
        assert (path.stat().st_mode & 0o777) == 0o600


@pytest.mark.unit
def test_sync_all_credentials_mirrors_newest_between_windows_and_wsl(tmp_path: Path) -> None:
    wsl_home = tmp_path / "wsl_home"
    win_home = tmp_path / "win_home"
    wsl_home.mkdir()
    win_home.mkdir()

    # 1. Newer Claude credential on Windows -> mirrors to both WSL paths with 0600
    win_claude = win_home / ".claude" / ".credentials.json"
    win_claude.parent.mkdir(parents=True)
    win_claude.write_text(
        json.dumps({"claudeAiOauth": {"refreshToken": "r-new", "accessToken": "a-new", "expiresAt": 2000}}),
        encoding="utf-8",
    )
    wsl_claude = wsl_home / ".claude" / ".credentials.json"
    wsl_claude.parent.mkdir(parents=True)
    wsl_claude.write_text(
        json.dumps({"claudeAiOauth": {"refreshToken": "r-old", "accessToken": "a-old", "expiresAt": 1000}}),
        encoding="utf-8",
    )
    os.utime(wsl_claude, (1_000_000, 1_000_000))
    os.utime(win_claude, (2_000_000, 2_000_000))

    # 2. Newer Codex credential on WSL -> mirrors to Windows
    wsl_codex = wsl_home / ".codex" / "auth.json"
    wsl_codex.parent.mkdir(parents=True)
    wsl_codex.write_text(
        json.dumps({"tokens": {"refresh_token": "cx-fresh", "access_token": "cx-acc"}}),
        encoding="utf-8",
    )

    # 3. Antigravity accounts on Windows -> mirrors to WSL with 0600
    win_agy = win_home / ".gemini" / "antigravity-cli"
    win_agy.mkdir(parents=True)
    (win_agy / "accounts.json").write_text(json.dumps([{"email": "user@example.com"}]), encoding="utf-8")
    (win_agy / "active_account.json").write_text(json.dumps({"email": "user@example.com"}), encoding="utf-8")

    synced = sync_mod.sync_all_credentials(wsl_home=wsl_home, win_homes=[win_home])

    rd_claude = wsl_home / ".config" / "runner-dashboard" / "claude" / ".credentials.json"
    assert rd_claude.is_file()
    assert json.loads(rd_claude.read_text(encoding="utf-8"))["claudeAiOauth"]["expiresAt"] == 2000
    assert json.loads(wsl_claude.read_text(encoding="utf-8"))["claudeAiOauth"]["expiresAt"] == 2000
    _assert_owner_only(rd_claude)
    _assert_owner_only(wsl_claude)

    win_codex = win_home / ".codex" / "auth.json"
    assert win_codex.is_file()
    assert json.loads(win_codex.read_text(encoding="utf-8"))["tokens"]["refresh_token"] == "cx-fresh"

    wsl_agy_acc = wsl_home / ".gemini" / "antigravity-cli" / "accounts.json"
    assert wsl_agy_acc.is_file()
    _assert_owner_only(wsl_agy_acc)
    assert synced["claude"] and synced["codex"] and synced["antigravity"]


@pytest.mark.unit
def test_claude_prefers_newer_refresh_token_over_higher_expires_at(tmp_path: Path) -> None:
    wsl_home = tmp_path / "wsl_home"
    win_home = tmp_path / "win_home"
    wsl_home.mkdir()
    win_home.mkdir()

    hub = wsl_home / ".config" / "runner-dashboard" / "claude" / ".credentials.json"
    hub.parent.mkdir(parents=True)
    hub.write_text(
        json.dumps({"claudeAiOauth": {"refreshToken": "r-old", "accessToken": "a-old", "expiresAt": 9000}}),
        encoding="utf-8",
    )

    win_claude = win_home / ".claude" / ".credentials.json"
    win_claude.parent.mkdir(parents=True)
    win_claude.write_text(
        json.dumps({"claudeAiOauth": {"refreshToken": "r-new", "accessToken": "a-new", "expiresAt": 1000}}),
        encoding="utf-8",
    )
    # Windows file is newer even though expiresAt is lower — hub must follow the refresh source.
    os.utime(hub, (1_000_000, 1_000_000))
    os.utime(win_claude, (2_000_000, 2_000_000))

    synced = sync_mod.sync_all_credentials(wsl_home=wsl_home, win_homes=[win_home])
    assert str(hub) in synced["claude"]
    assert json.loads(hub.read_text(encoding="utf-8"))["claudeAiOauth"]["refreshToken"] == "r-new"


@pytest.mark.unit
def test_sync_skips_unreadable_source_without_crashing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    wsl_home = tmp_path / "wsl_home"
    win_home = tmp_path / "win_home"
    wsl_home.mkdir()
    win_home.mkdir()
    good = wsl_home / ".claude" / ".credentials.json"
    good.parent.mkdir(parents=True)
    good.write_text(
        json.dumps({"claudeAiOauth": {"refreshToken": "r-ok", "accessToken": "a-ok", "expiresAt": 2000}}),
        encoding="utf-8",
    )

    blocked = win_home / ".claude" / ".credentials.json"
    blocked.parent.mkdir(parents=True)
    blocked.write_text(
        json.dumps({"claudeAiOauth": {"refreshToken": "r-blocked", "accessToken": "a-blocked", "expiresAt": 9999}}),
        encoding="utf-8",
    )
    blocked.chmod(0o000)
    try:
        with caplog.at_level("WARNING"):
            synced = sync_mod.sync_all_credentials(wsl_home=wsl_home, win_homes=[win_home])
    finally:
        blocked.chmod(0o600)
    assert any("skipping" in rec.message.lower() for rec in caplog.records)
    hub = wsl_home / ".config" / "runner-dashboard" / "claude" / ".credentials.json"
    assert hub.is_file()
    assert json.loads(hub.read_text(encoding="utf-8"))["claudeAiOauth"]["refreshToken"] == "r-ok"
    assert "r-blocked" not in caplog.text
    assert synced["claude"]


@pytest.mark.unit
def test_sync_result_json_lists_paths_only(tmp_path: Path) -> None:
    wsl_home = tmp_path / "wsl_home"
    wsl_home.mkdir()
    cred = wsl_home / ".claude" / ".credentials.json"
    cred.parent.mkdir(parents=True)
    cred.write_text(
        json.dumps({"claudeAiOauth": {"refreshToken": "r-out", "accessToken": "acc", "expiresAt": 3000}}),
        encoding="utf-8",
    )
    synced = sync_mod.sync_all_credentials(wsl_home=wsl_home, win_homes=[])
    blob = json.dumps(synced)
    assert "r-out" not in blob
    assert "acc" not in blob

