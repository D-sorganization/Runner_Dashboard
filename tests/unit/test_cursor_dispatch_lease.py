"""Unit tests for the Cursor dispatch pre-launch/pre-publish lease hook (#3931 S2).

Clean-room: built from public docs/behavior descriptions only.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_lease_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_PATH_LEASE_DB_PATH", str(tmp_path / "path_leases.sqlite3"))


def test_cursor_dispatch_scope_prefers_issue_over_pr() -> None:
    from coordination.cursor_dispatch_lease import cursor_dispatch_scope

    assert cursor_dispatch_scope(42, 7) == "issue:42"
    assert cursor_dispatch_scope(None, 7) == "pr:7"


def test_cursor_dispatch_scope_requires_a_target() -> None:
    from coordination.cursor_dispatch_lease import cursor_dispatch_scope

    with pytest.raises(ValueError, match="issue or PR number"):
        cursor_dispatch_scope(None, None)


def test_pre_launch_recheck_acquires_when_free() -> None:
    from coordination.cursor_dispatch_lease import pre_launch_lease_recheck

    lease = pre_launch_lease_recheck("Runner_Dashboard", 101, None, "run-a", 3600.0)
    assert lease is not None
    assert lease.repo == "Runner_Dashboard"
    assert lease.scope == "issue:101"
    assert lease.owner == "run-a"


def test_pre_launch_recheck_blocks_when_another_owner_holds_it() -> None:
    from coordination.cursor_dispatch_lease import pre_launch_lease_recheck

    first = pre_launch_lease_recheck("Runner_Dashboard", 101, None, "run-a", 3600.0)
    assert first is not None

    second = pre_launch_lease_recheck("Runner_Dashboard", 101, None, "run-b", 3600.0)
    assert second is None


def test_pre_launch_recheck_is_idempotent_for_the_same_owner() -> None:
    from coordination.cursor_dispatch_lease import pre_launch_lease_recheck

    first = pre_launch_lease_recheck("Runner_Dashboard", 101, None, "run-a", 3600.0)
    second = pre_launch_lease_recheck("Runner_Dashboard", 101, None, "run-a", 3600.0)
    assert first is not None
    assert second is not None
    assert second.lease_id == first.lease_id


def test_pre_publish_recheck_true_when_unleased() -> None:
    from coordination.cursor_dispatch_lease import pre_publish_lease_recheck

    assert pre_publish_lease_recheck("Runner_Dashboard", 202, None, "run-a") is True


def test_pre_publish_recheck_true_when_still_held_by_owner() -> None:
    from coordination.cursor_dispatch_lease import pre_launch_lease_recheck, pre_publish_lease_recheck

    pre_launch_lease_recheck("Runner_Dashboard", 202, None, "run-a", 3600.0)
    assert pre_publish_lease_recheck("Runner_Dashboard", 202, None, "run-a") is True


def test_pre_publish_recheck_false_when_another_owner_took_it_over() -> None:
    from coordination.cursor_dispatch_lease import pre_launch_lease_recheck, pre_publish_lease_recheck
    from path_lease_store import PathLeaseStore, default_path_lease_db_path

    lease = pre_launch_lease_recheck("Runner_Dashboard", 202, None, "run-a", 3600.0)
    assert lease is not None
    store = PathLeaseStore(default_path_lease_db_path())
    store.release(lease.lease_id, "run-a")
    store.acquire("Runner_Dashboard", "issue:202", "run-b", 3600.0)

    assert pre_publish_lease_recheck("Runner_Dashboard", 202, None, "run-a") is False


def test_release_lease_quietly_frees_the_scope() -> None:
    from coordination.cursor_dispatch_lease import pre_launch_lease_recheck, release_lease_quietly
    from path_lease_store import PathLeaseStore, default_path_lease_db_path

    lease = pre_launch_lease_recheck("Runner_Dashboard", 303, None, "run-a", 3600.0)
    assert lease is not None
    release_lease_quietly(lease)

    store = PathLeaseStore(default_path_lease_db_path())
    assert store.check("Runner_Dashboard", "issue:303") is None


def test_release_lease_quietly_does_not_raise_when_already_released() -> None:
    from coordination.cursor_dispatch_lease import pre_launch_lease_recheck, release_lease_quietly

    lease = pre_launch_lease_recheck("Runner_Dashboard", 404, None, "run-a", 3600.0)
    assert lease is not None
    release_lease_quietly(lease)
    release_lease_quietly(lease)  # second release: must fail open, not raise
