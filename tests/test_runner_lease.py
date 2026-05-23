"""Tests for backend/runner_lease.py — issue #386."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import identity as id_mod
import pytest
import runner_lease as rl


def _make_principal(
    pid: str = "p1",
    max_runners: int = 2,
) -> id_mod.Principal:
    return id_mod.Principal(
        id=pid,
        type="bot",
        name="Test Bot",
        roles=["operator"],
        quotas=id_mod.Quota(max_runners=max_runners),
    )


def _make_manager(tmp_path: Path) -> rl.LeaseManager:
    # Bypass security check for tmp_path
    with patch("runner_lease.validate_config_path"):
        mgr = rl.LeaseManager(config_dir=tmp_path)
    return mgr


# ---------------------------------------------------------------------------
# LeaseRecord
# ---------------------------------------------------------------------------


def test_lease_record_fields() -> None:
    rec = rl.LeaseRecord(
        principal_id="p1",
        runner_id="runner-1",
        acquired_at=1000.0,
        expires_at=4600.0,
    )
    assert rec.runner_id == "runner-1"
    assert rec.expires_at == 4600.0


# ---------------------------------------------------------------------------
# LeaseManager.acquire_lease — happy path
# ---------------------------------------------------------------------------


def test_acquire_lease_happy(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    principal = _make_principal()
    with patch("runner_lease.validate_config_path"), patch.object(mgr, "save_leases"):
        rec = mgr.acquire_lease(principal, "runner-1")
    assert rec.principal_id == "p1"
    assert rec.runner_id == "runner-1"
    assert rec.expires_at is not None


# ---------------------------------------------------------------------------
# acquire_lease — quota exceeded
# ---------------------------------------------------------------------------


def test_acquire_lease_quota_exceeded(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    principal = _make_principal(max_runners=1)
    with patch("runner_lease.validate_config_path"), patch.object(mgr, "save_leases"):
        mgr.acquire_lease(principal, "runner-1")
        with pytest.raises(PermissionError, match="quota"):
            mgr.acquire_lease(principal, "runner-2")


# ---------------------------------------------------------------------------
# acquire_lease — runner already leased by another principal
# ---------------------------------------------------------------------------


def test_acquire_lease_runner_already_leased(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    p1 = _make_principal("p1", max_runners=5)
    p2 = _make_principal("p2", max_runners=5)
    with patch("runner_lease.validate_config_path"), patch.object(mgr, "save_leases"):
        mgr.acquire_lease(p1, "runner-1")
        with pytest.raises(ValueError, match="already leased"):
            mgr.acquire_lease(p2, "runner-1")


# ---------------------------------------------------------------------------
# release_lease
# ---------------------------------------------------------------------------


def test_release_lease_removes_entry(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    principal = _make_principal()
    with patch("runner_lease.validate_config_path"), patch.object(mgr, "save_leases"):
        mgr.acquire_lease(principal, "runner-1")
        mgr.release_lease("runner-1", "p1")
    assert mgr.get_active_leases("p1") == []


# ---------------------------------------------------------------------------
# prune_expired
# ---------------------------------------------------------------------------


def test_prune_expired_removes_old_leases(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    # Insert an expired lease directly
    mgr.leases.append(
        rl.LeaseRecord(
            principal_id="p1",
            runner_id="old-runner",
            acquired_at=time.time() - 7200,
            expires_at=time.time() - 3600,  # expired 1h ago
        )
    )
    with patch.object(mgr, "save_leases"):
        mgr.prune_expired()
    assert not any(r.runner_id == "old-runner" for r in mgr.leases)
