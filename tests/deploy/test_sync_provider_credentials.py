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


def _zeroed_newer_copy_over_valid_hub(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Hub seat holds a real expiry; a newer Windows copy carries expiresAt 0 (#3971)."""
    wsl_home = tmp_path / "wsl_home"
    win_home = tmp_path / "win_home"
    hub = wsl_home / ".config" / "runner-dashboard" / "claude" / ".credentials.json"
    hub.parent.mkdir(parents=True)
    hub.write_text(
        json.dumps({"claudeAiOauth": {"refreshToken": "r-valid", "accessToken": "a-valid", "expiresAt": 9000}}),
        encoding="utf-8",
    )
    win_claude = win_home / ".claude" / ".credentials.json"
    win_claude.parent.mkdir(parents=True)
    win_claude.write_text(
        json.dumps({"claudeAiOauth": {"refreshToken": "r-zero", "accessToken": "a-zero", "expiresAt": 0}}),
        encoding="utf-8",
    )
    os.utime(hub, (1_000_000, 1_000_000))
    os.utime(win_claude, (2_000_000, 2_000_000))
    return wsl_home, win_home, hub


@pytest.mark.unit
def test_claude_sync_never_mirrors_expires_at_zero_over_valid_seat(tmp_path: Path) -> None:
    wsl_home, win_home, hub = _zeroed_newer_copy_over_valid_hub(tmp_path)

    synced = sync_mod.sync_all_credentials(wsl_home=wsl_home, win_homes=[win_home])

    assert str(hub) not in synced["claude"]
    assert json.loads(hub.read_text(encoding="utf-8"))["claudeAiOauth"]["expiresAt"] == 9000


@pytest.mark.unit
def test_claude_audit_source_is_valid_seat_not_expires_at_zero_copy(tmp_path: Path) -> None:
    wsl_home, win_home, hub = _zeroed_newer_copy_over_valid_hub(tmp_path)

    report = sync_mod.audit_credential_mirrors(wsl_home=wsl_home, win_homes=[win_home])

    assert report["providers"]["claude"]["source_path"] == str(hub)


@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="chmod(0o000) does not block reads on Windows")
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
def test_audit_flags_stale_claude_mirror(tmp_path: Path) -> None:
    wsl_home = tmp_path / "wsl_home"
    win_home = tmp_path / "win_home"
    wsl_home.mkdir()
    win_home.mkdir()

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

    audit = sync_mod.audit_credential_mirrors(wsl_home=wsl_home, win_homes=[win_home])
    assert audit["ok"] is False
    claude = audit["providers"]["claude"]
    assert claude["ok"] is False
    assert any(row["reason"] in ("older_than_source", "content_drift") for row in claude["stale_copies"])
    blob = json.dumps(audit)
    assert "r-old" not in blob and "r-new" not in blob


@pytest.mark.unit
def test_audit_ignores_subsecond_mtime_truncation_on_identical_copy(tmp_path: Path) -> None:
    """DrvFs (/mnt/c) keeps whole-second mtimes; a byte-identical mirror is in sync, not stale."""
    wsl_home = tmp_path / "wsl_home"
    win_home = tmp_path / "win_home"
    payload = json.dumps({"claudeAiOauth": {"refreshToken": "r-same", "accessToken": "a-same", "expiresAt": 3000}})
    hub = wsl_home / ".config" / "runner-dashboard" / "claude" / ".credentials.json"
    win_claude = win_home / ".claude" / ".credentials.json"
    for path in (hub, win_claude):
        path.parent.mkdir(parents=True)
        path.write_text(payload, encoding="utf-8")
    os.utime(hub, (1_000_000.887, 1_000_000.887))
    os.utime(win_claude, (1_000_000.0, 1_000_000.0))

    audit = sync_mod.audit_credential_mirrors(wsl_home=wsl_home, win_homes=[win_home])

    assert audit["providers"]["claude"]["stale_copies"] == []
    assert audit["providers"]["claude"]["ok"] is True


@pytest.mark.unit
def test_audit_ok_after_sync(tmp_path: Path) -> None:
    wsl_home = tmp_path / "wsl_home"
    wsl_home.mkdir()
    cred = wsl_home / ".claude" / ".credentials.json"
    cred.parent.mkdir(parents=True)
    cred.write_text(
        json.dumps({"claudeAiOauth": {"refreshToken": "r-only", "accessToken": "a-only", "expiresAt": 3000}}),
        encoding="utf-8",
    )
    sync_mod.sync_all_credentials(wsl_home=wsl_home, win_homes=[])
    audit = sync_mod.audit_credential_mirrors(wsl_home=wsl_home, win_homes=[])
    assert audit["providers"]["claude"]["ok"] is True


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


@pytest.mark.unit
def test_claude_mirror_write_logs_audit_line_with_caller_and_expiry_flag(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """RD #3975: every Claude mirror write must log its source and an expiresAt>0 flag, no secrets."""
    wsl_home = tmp_path / "wsl_home"
    wsl_home.mkdir()
    cred = wsl_home / ".claude" / ".credentials.json"
    cred.parent.mkdir(parents=True)
    cred.write_text(
        json.dumps({"claudeAiOauth": {"refreshToken": "r-secret", "accessToken": "a-secret", "expiresAt": 3000}}),
        encoding="utf-8",
    )
    with caplog.at_level("INFO"):
        synced = sync_mod.sync_all_credentials(wsl_home=wsl_home, win_homes=[])
    assert synced["claude"]
    audit_records = [rec for rec in caplog.records if "credential mirror write" in rec.message]
    assert audit_records, "expected an audit log line for the Claude mirror write"
    assert any("expires_at_positive=True" in rec.getMessage() for rec in audit_records)
    assert "r-secret" not in caplog.text and "a-secret" not in caplog.text


@pytest.mark.unit
def test_cli_reports_structured_error_and_exits_nonzero_on_sync_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """RD #3975: the CLI must fail loudly (non-zero exit + JSON error summary), not silently.

    Self-heal previously had no way to tell a crashed sync from a clean one: the script
    propagated a raw traceback and a non-JSON exit instead of a single parseable line.
    """

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("secret-token-xyz should never reach the summary")

    monkeypatch.setattr(sync_mod, "sync_all_credentials", _boom)
    exit_code = sync_mod._cli_main()
    assert exit_code != 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["error"].startswith("RuntimeError:")
    assert "Traceback" not in out
