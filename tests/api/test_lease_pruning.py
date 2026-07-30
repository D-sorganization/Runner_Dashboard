"""Tests for background lease reaper (issue #708).

Pruning performs a locked read-modify-write that re-reads leases.yml from disk
(issue #936), so each manager persists its leases before pruning and the
allowed-roots security guard is bypassed for the throwaway tmp config dir.
"""

import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from runner_lease import LeaseManager, LeaseRecord


def make_lease(runner_id, expires_delta, principal_id="test"):
    return LeaseRecord(
        principal_id=principal_id,
        runner_id=runner_id,
        acquired_at=time.time(),
        expires_at=time.time() + expires_delta,
    )


def _persist(mgr, records):
    mgr.leases = list(records)
    mgr.save_leases()


def test_prune_expired_returns_count():
    with tempfile.TemporaryDirectory() as tmpdir, patch("runner_lease.validate_config_path"):
        mgr = LeaseManager(config_dir=Path(tmpdir))
        _persist(
            mgr,
            [
                make_lease("runner-1", -1),  # expired
                make_lease("runner-2", 3600),  # active
            ],
        )
        count = mgr.prune_expired()
        assert isinstance(count, int), "prune_expired must return int"
        assert count == 1


def test_prune_expired_returns_zero_when_none_expired():
    with tempfile.TemporaryDirectory() as tmpdir, patch("runner_lease.validate_config_path"):
        mgr = LeaseManager(config_dir=Path(tmpdir))
        _persist(mgr, [make_lease("runner-1", 3600)])
        count = mgr.prune_expired()
        assert count == 0


def test_prune_expired_empty_lease_list():
    with tempfile.TemporaryDirectory() as tmpdir, patch("runner_lease.validate_config_path"):
        mgr = LeaseManager(config_dir=Path(tmpdir))
        _persist(mgr, [])
        count = mgr.prune_expired()
        assert count == 0


def test_prune_expired_removes_expired_from_list():
    with tempfile.TemporaryDirectory() as tmpdir, patch("runner_lease.validate_config_path"):
        mgr = LeaseManager(config_dir=Path(tmpdir))
        _persist(
            mgr,
            [
                make_lease("runner-1", -100),  # expired
                make_lease("runner-2", -50),  # expired
                make_lease("runner-3", 3600),  # active
            ],
        )
        count = mgr.prune_expired()
        assert count == 2
        assert len(mgr.leases) == 1
        assert mgr.leases[0].runner_id == "runner-3"


def test_prune_expired_none_expires_at_not_pruned():
    """Leases with expires_at=None should never be pruned."""
    with tempfile.TemporaryDirectory() as tmpdir, patch("runner_lease.validate_config_path"):
        mgr = LeaseManager(config_dir=Path(tmpdir))
        _persist(
            mgr,
            [
                LeaseRecord(
                    principal_id="test",
                    runner_id="runner-permanent",
                    acquired_at=time.time(),
                    expires_at=None,
                )
            ],
        )
        count = mgr.prune_expired()
        assert count == 0
        assert len(mgr.leases) == 1
