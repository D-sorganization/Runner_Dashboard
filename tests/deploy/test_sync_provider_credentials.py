"""Tests for bi-directional Windows <-> WSL provider credential mirroring."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "sync_provider_credentials.py"
_SPEC = importlib.util.spec_from_file_location("sync_provider_credentials", _SCRIPT)
assert _SPEC and _SPEC.loader
sync_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sync_mod)


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
    assert (rd_claude.stat().st_mode & 0o777) == 0o600
    assert (wsl_claude.stat().st_mode & 0o777) == 0o600

    win_codex = win_home / ".codex" / "auth.json"
    assert win_codex.is_file()
    assert json.loads(win_codex.read_text(encoding="utf-8"))["tokens"]["refresh_token"] == "cx-fresh"

    wsl_agy_acc = wsl_home / ".gemini" / "antigravity-cli" / "accounts.json"
    assert wsl_agy_acc.is_file()
    assert (wsl_agy_acc.stat().st_mode & 0o777) == 0o600
    assert synced["claude"] and synced["codex"] and synced["antigravity"]
