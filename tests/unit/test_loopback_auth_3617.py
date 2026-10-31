"""Loopback auth perimeter and token diagnostics (#3617)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import dashboard_config
import identity as id_mod


def test_resolve_bearer_token_reports_hash_not_registered(tmp_path: Path) -> None:
    mgr = id_mod.IdentityManager(config_dir=tmp_path)
    principal, reason = mgr.resolve_bearer_token("not-a-registered-token")
    assert principal is None
    assert reason == "token_hash_not_registered"


def test_resolve_bearer_token_reports_expired_token(tmp_path: Path) -> None:
    import hashlib
    import time

    mgr = id_mod.IdentityManager(config_dir=tmp_path)
    mgr.principals["agent"] = id_mod.Principal(id="agent", type="bot", name="Agent")
    raw = "svc-expired"
    mgr.tokens.append(
        id_mod.TokenRecord(
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            principal_id="agent",
            created_at=time.time(),
            expires_at=time.time() - 10,
            name="expired",
        )
    )
    principal, reason = mgr.resolve_bearer_token(raw)
    assert principal is None
    assert reason == "token_expired"


def test_token_store_health_snapshot_detects_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = id_mod.IdentityManager(config_dir=tmp_path)
    mgr.tokens.append(
        id_mod.TokenRecord(
            token_hash="abc",
            principal_id="missing",
            created_at=0.0,
            expires_at=None,
            name="orphan",
        )
    )
    monkeypatch.setattr(id_mod, "identity_manager", mgr)
    snapshot = id_mod.token_store_health_snapshot()
    assert snapshot["loaded_token_count"] == 1
    assert snapshot["on_disk_token_count"] == 0
    assert snapshot["in_sync"] is False


def test_validate_loopback_auth_bind_rejects_all_interfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "1")
    monkeypatch.setattr(dashboard_config, "HOST", "0.0.0.0")
    with pytest.raises(RuntimeError, match="loopback-only"):
        dashboard_config.validate_loopback_auth_bind()


def test_validate_loopback_auth_bind_allows_loopback_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "1")
    monkeypatch.setattr(dashboard_config, "HOST", "127.0.0.1")
    dashboard_config.validate_loopback_auth_bind()
