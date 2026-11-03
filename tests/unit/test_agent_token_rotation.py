"""Agent token auto-rotation (#3895 slice 3).

Every token is a fixture minted into a temporary identity store; no log line
may carry a token value or hash.
"""

from __future__ import annotations

import logging
import stat
import time
from pathlib import Path

import pytest
from agent_token_rotation import (
    GRACE_DAYS,
    ROTATE_AGE_DAYS,
    due_rotations,
    rotate_token,
    sweep_grace_expired,
)
from identity import IdentityManager

_DAY = 86400.0


@pytest.fixture
def id_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    defaults = tmp_path / "agent_principals.yml"
    defaults.write_text("agents:\n  - id: agent-grok\n    kind: grokbot\n", encoding="utf-8")
    monkeypatch.setenv("DASHBOARD_AGENT_PRINCIPALS_FILE", str(defaults))
    monkeypatch.delenv("RD_OVERLAY_PATH", raising=False)
    path = tmp_path / "id"
    (path / "agent-tokens").mkdir(parents=True)
    return path


def _minted(id_dir: Path, days: int) -> tuple[IdentityManager, str, Path]:
    token_file = id_dir / "agent-tokens" / "agent-grok.token"
    token_file.write_text("svc_fixture_bootstrap\n", encoding="utf-8")
    mgr = IdentityManager(config_dir=id_dir)
    raw = mgr.mint_service_token("agent-grok", name="agent-tokens/agent-grok.token", expires_in_days=days)
    token_file.write_text(raw + "\n", encoding="utf-8")
    return mgr, raw, token_file


def test_not_due_when_fresh_and_far_from_expiry(id_dir: Path) -> None:
    mgr, _raw, _path = _minted(id_dir, days=90)
    assert due_rotations(mgr) == []


def test_due_when_remaining_life_below_threshold(id_dir: Path) -> None:
    mgr, _raw, _path = _minted(id_dir, days=10)
    assert due_rotations(mgr) == ["agent-grok"]


def _record_for(mgr: IdentityManager, raw: str):
    import hashlib

    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    return next(t for t in mgr.tokens if t.token_hash == token_hash)


def test_due_when_token_age_crosses_threshold(id_dir: Path) -> None:
    mgr, raw, _path = _minted(id_dir, days=200)
    _record_for(mgr, raw).created_at -= (ROTATE_AGE_DAYS + 1) * _DAY
    assert due_rotations(mgr) == ["agent-grok"]


def test_not_due_for_a_missing_or_rejected_token(id_dir: Path) -> None:
    mgr = IdentityManager(config_dir=id_dir)
    assert due_rotations(mgr) == []


def test_rotate_writes_a_new_token_file_mode_600(id_dir: Path) -> None:
    mgr, raw, path = _minted(id_dir, days=10)
    rotate_token(mgr, "agent-grok")
    new_raw = path.read_text(encoding="utf-8").strip()
    assert new_raw != raw
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_rotate_keeps_the_prior_token_valid(id_dir: Path) -> None:
    mgr, raw, _path = _minted(id_dir, days=10)
    rotate_token(mgr, "agent-grok")
    principal, reason = mgr.resolve_bearer_token(raw)
    assert principal is not None
    assert reason is None


def test_rotate_lets_the_new_token_authenticate_immediately(id_dir: Path) -> None:
    mgr, _raw, path = _minted(id_dir, days=10)
    rotate_token(mgr, "agent-grok")
    new_raw = path.read_text(encoding="utf-8").strip()
    principal, reason = mgr.resolve_bearer_token(new_raw)
    assert principal is not None
    assert reason is None


def test_sweep_does_not_revoke_within_the_grace_window(id_dir: Path) -> None:
    mgr, raw, _path = _minted(id_dir, days=10)
    rotate_token(mgr, "agent-grok")
    assert sweep_grace_expired(mgr) == []
    principal, _reason = mgr.resolve_bearer_token(raw)
    assert principal is not None


def test_sweep_revokes_the_prior_token_once_grace_elapses(id_dir: Path) -> None:
    mgr, raw, _path = _minted(id_dir, days=10)
    rotate_token(mgr, "agent-grok")
    future = time.time() + (GRACE_DAYS + 1) * _DAY
    revoked = sweep_grace_expired(mgr, now=future)
    assert revoked and set(revoked) == {"agent-grok"}
    principal, reason = mgr.resolve_bearer_token(raw)
    assert principal is None
    assert reason == "token_hash_not_registered"


def test_rotation_never_logs_a_token_value(id_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    mgr, raw, path = _minted(id_dir, days=10)
    with caplog.at_level(logging.INFO):
        rotate_token(mgr, "agent-grok")
    new_raw = path.read_text(encoding="utf-8").strip()
    assert raw not in caplog.text
    assert new_raw not in caplog.text
    assert all(t.token_hash not in caplog.text for t in mgr.tokens)
