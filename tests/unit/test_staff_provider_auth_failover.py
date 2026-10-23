"""Provider auth failover at dispatch planning (#2170)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from staff.adapters import ADAPTERS  # noqa: E402
from staff.failover_dispatch import resolve_provider_and_model  # noqa: E402
from staff.plan import RunRequest  # noqa: E402
from staff.provider_failover_config import FailoverStep  # noqa: E402
from staff.roles import RoleSpec  # noqa: E402


def _role() -> RoleSpec:
    return RoleSpec(
        name="night-watch",
        title="Night Watch",
        providers=("claude", "codex", "antigravity"),
    )


@pytest.mark.unit
def test_skips_auth_expired_provider_in_chain(staff_cli_installed) -> None:
    staff_cli_installed("claude", "codex", "antigravity")
    req = RunRequest(role="night-watch", prompt="work", repo="Runner_Dashboard")
    role = _role()

    def _refusal(provider: str, _machine: str):
        if provider == "claude":
            return type("R", (), {"failure_class": "auth_expired"})()
        return None

    with (
        patch("staff.failover_dispatch._provider_auth_blocks", side_effect=lambda p, m: _refusal(p, m) is not None),
        patch(
            "staff.failover_dispatch.resolve_failover_chain",
            return_value=(FailoverStep("claude"), FailoverStep("codex"), FailoverStep("antigravity")),
        ),
    ):
        provider, _model, _tier, skipped = resolve_provider_and_model(ADAPTERS, "Desk", req, role)
    assert provider != "claude"
    assert any(row.get("provider") == "claude" for row in skipped)


@pytest.mark.unit
def test_provider_auth_preflight_classifies_expiring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from staff.provider_auth_preflight import probe_local_provider_auth

    cred_dir = tmp_path / "claude"
    cred_dir.mkdir()
    cred = cred_dir / ".credentials.json"
    soon = time.time() + 3600
    cred.write_text(f'{{"expiresAt": {int(soon * 1000)}}}', encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cred_dir))
    monkeypatch.setattr("staff.cli_oauth_session.resolve_claude_config_dir", lambda: cred_dir)

    with patch("staff.cli_oauth_session.claude_oauth_session_alive", return_value=(True, "ok")):
        report = probe_local_provider_auth("Desk")
    assert report["providers"]["claude"]["status"] == "expiring"


@pytest.mark.unit
def test_provider_auth_preflight_marks_stale_codex_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import time

    from staff.provider_auth_preflight import probe_local_provider_auth

    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    auth = codex_home / "auth.json"
    auth.write_text('{"refresh_token":"x","access_token":"y"}', encoding="utf-8")
    old = time.time() - (10 * 86400)
    os.utime(auth, (old, old))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("STAFF_PROVIDER_AUTH_STALE_DAYS", "7")

    with patch("staff.provider_auth_preflight.provider_oauth_session_alive", return_value=(True, "ok")):
        report = probe_local_provider_auth("Desk")
    assert report["providers"]["codex"]["status"] == "stale"


@pytest.mark.unit
def test_provider_auth_preflight_ok_with_refresh_token_and_4h_keepalive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    from staff.provider_auth_preflight import (
        _KEEPALIVE_INTERVAL_SECONDS,
        _seconds_until_next_evening_pt,
        probe_local_provider_auth,
    )

    cred_dir = tmp_path / "claude"
    cred_dir.mkdir()
    cred = cred_dir / ".credentials.json"
    eight_hours = time.time() + 8 * 3600
    cred.write_text(
        f'{{"expiresAt": {int(eight_hours * 1000)}, "refreshToken": "rt-seat"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cred_dir))
    monkeypatch.setattr("staff.cli_oauth_session.resolve_claude_config_dir", lambda: cred_dir)

    with patch("staff.cli_oauth_session.claude_oauth_session_alive", return_value=(True, "ok")):
        report = probe_local_provider_auth("Brick")
    assert report["providers"]["claude"]["status"] == "ok"
    assert report["providers"]["claude"]["has_refresh_token"] is True
    assert _KEEPALIVE_INTERVAL_SECONDS == 4 * 3600
    assert _seconds_until_next_evening_pt() <= 4 * 3600
