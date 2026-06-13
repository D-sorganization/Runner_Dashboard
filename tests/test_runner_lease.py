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


@pytest.fixture
def _unguard_config_path():
    """Disable the allowed-roots security check so leases can persist under tmp_path.

    prune_expired now performs a locked read-modify-write (issue #936) that
    re-reads leases.yml from disk; tests exercising it must let the manager
    actually write to, and load from, the tmp config dir. load_leases routes
    through ``security.safe_yaml_load`` (which validates via the ``security``
    module reference), so both references are patched.
    """
    with (
        patch("runner_lease.validate_config_path"),
        patch("security.validate_config_path", side_effect=lambda p, *a, **k: p),
    ):
        yield


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


def test_release_lease_removes_entry(tmp_path: Path, _unguard_config_path) -> None:
    mgr = _make_manager(tmp_path)
    principal = _make_principal()
    mgr.acquire_lease(principal, "runner-1")
    mgr.release_lease("runner-1", "p1")
    assert mgr.get_active_leases("p1") == []


# ---------------------------------------------------------------------------
# prune_expired
# ---------------------------------------------------------------------------


def _persist(mgr: rl.LeaseManager, records: list[rl.LeaseRecord]) -> None:
    """Write *records* to the manager's leases.yml so a locked re-read sees them."""
    mgr.leases = list(records)
    mgr.save_leases()


def test_prune_expired_removes_old_leases(tmp_path: Path, _unguard_config_path) -> None:
    mgr = _make_manager(tmp_path)
    _persist(
        mgr,
        [
            rl.LeaseRecord(
                principal_id="p1",
                runner_id="old-runner",
                acquired_at=time.time() - 7200,
                expires_at=time.time() - 3600,  # expired 1h ago
            )
        ],
    )
    mgr.prune_expired()
    assert not any(r.runner_id == "old-runner" for r in mgr.leases)


# ---------------------------------------------------------------------------
# prune_expired count return — A2 (lease reaper observability)
# ---------------------------------------------------------------------------


def test_prune_expired_returns_count_of_removed_leases(tmp_path: Path, _unguard_config_path) -> None:
    """A2: prune_expired must return the number of removed entries so the
    background reaper can emit accurate metrics and log lines."""
    mgr = _make_manager(tmp_path)
    now = time.time()
    _persist(
        mgr,
        [
            rl.LeaseRecord(principal_id="p1", runner_id="r-old-1", acquired_at=now - 7200, expires_at=now - 3600),
            rl.LeaseRecord(principal_id="p1", runner_id="r-old-2", acquired_at=now - 7200, expires_at=now - 100),
            rl.LeaseRecord(principal_id="p2", runner_id="r-fresh", acquired_at=now, expires_at=now + 3600),
            rl.LeaseRecord(principal_id="p3", runner_id="r-perm", acquired_at=now, expires_at=None),
        ],
    )
    removed = mgr.prune_expired()
    # Post-condition: returns an int matching the number actually pruned.
    assert isinstance(removed, int)
    assert removed == 2
    # And the in-memory list reflects the removal.
    assert {r.runner_id for r in mgr.leases} == {"r-fresh", "r-perm"}


def test_prune_expired_returns_zero_when_nothing_to_prune(tmp_path: Path, _unguard_config_path) -> None:
    """A2: when no leases are expired, the reaper must see 0 — never None."""
    mgr = _make_manager(tmp_path)
    now = time.time()
    _persist(mgr, [rl.LeaseRecord(principal_id="p1", runner_id="r-fresh", acquired_at=now, expires_at=now + 3600)])
    removed = mgr.prune_expired()
    assert removed == 0
    assert len(mgr.leases) == 1


def test_prune_expired_returns_zero_on_empty_store(tmp_path: Path, _unguard_config_path) -> None:
    """A2: empty lease store is a valid steady-state for the reaper."""
    mgr = _make_manager(tmp_path)
    removed = mgr.prune_expired()
    assert removed == 0


def test_prune_does_not_clobber_concurrent_acquisition(tmp_path: Path, _unguard_config_path) -> None:
    """Issue #936: a lease written between this manager's load and its prune
    must survive the prune (no unlocked stale-snapshot clobber)."""
    now = time.time()
    writer = _make_manager(tmp_path)
    _persist(writer, [rl.LeaseRecord(principal_id="p1", runner_id="r-old", acquired_at=now - 7200, expires_at=now - 1)])

    # Manager A loads the single (expired) lease.
    mgr_a = _make_manager(tmp_path)
    assert {r.runner_id for r in mgr_a.leases} == {"r-old"}

    # Process B acquires a fresh lease *after* A loaded its snapshot.
    mgr_b = _make_manager(tmp_path)
    mgr_b.leases.append(rl.LeaseRecord(principal_id="p2", runner_id="r-new", acquired_at=now, expires_at=now + 3600))
    mgr_b.save_leases()

    # A prunes: it must re-read under lock, drop only the expired r-old, and
    # preserve B's r-new rather than overwriting it from A's stale snapshot.
    removed = mgr_a.prune_expired()
    assert removed == 1
    assert {r.runner_id for r in mgr_a.leases} == {"r-new"}
    # And the on-disk file agrees.
    reloaded = _make_manager(tmp_path)
    assert {r.runner_id for r in reloaded.leases} == {"r-new"}
