"""Tests for backend/runner_lease_migration.py — issue #3877."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import runner_lease as rl
import runner_lease_migration as mig


class _FakeStore:
    """In-memory stand-in for a second ``LeaseStore`` backend.

    #3878's real SQLite backend does not exist yet; this fake is what the
    harness is actually exercised against here, since the harness is
    backend-agnostic by design.
    """

    def __init__(self, records: list[rl.LeaseRecord] | None = None):
        self._records = list(records or [])

    def read_all(self) -> list[rl.LeaseRecord]:
        return list(self._records)

    def write_all(self, records: list[rl.LeaseRecord]) -> None:
        self._records = list(records)


def _record(runner_id: str, principal_id: str = "p1") -> rl.LeaseRecord:
    return rl.LeaseRecord(principal_id=principal_id, runner_id=runner_id, acquired_at=0.0, expires_at=None)


@pytest.fixture
def _unguard_config_path():
    """Disable the allowed-roots security check so leases can persist under tmp_path.

    Mirrors ``tests/test_runner_lease.py``'s fixture of the same name: both the
    ``runner_lease`` and ``security`` module references to
    ``validate_config_path`` must be patched since ``load_leases`` routes
    through ``security.safe_yaml_load``.
    """
    with (
        patch("runner_lease.validate_config_path"),
        patch("security.validate_config_path", side_effect=lambda p, *a, **k: p),
    ):
        yield


@pytest.fixture
def _yaml_manager(tmp_path: Path, _unguard_config_path) -> rl.LeaseManager:
    return rl.LeaseManager(config_dir=tmp_path)


def test_yaml_lease_store_round_trips_through_lease_manager(
    _yaml_manager: rl.LeaseManager, _unguard_config_path
) -> None:
    store = mig.YamlLeaseStore(_yaml_manager)
    assert store.read_all() == []

    store.write_all([_record("r1"), _record("r2")])

    assert {r.runner_id for r in store.read_all()} == {"r1", "r2"}
    # The adapter persisted through the manager's own file, not just memory.
    reloaded = mig.YamlLeaseStore(_yaml_manager)
    assert {r.runner_id for r in reloaded.read_all()} == {"r1", "r2"}


def test_harness_starts_with_primary_authority() -> None:
    harness = mig.LeaseMigrationHarness(_FakeStore(), _FakeStore())
    assert harness.authority == "primary"
    assert harness.authoritative_store() is harness._primary


def test_read_dual_reports_agreement_when_backends_match() -> None:
    primary = _FakeStore([_record("r1")])
    secondary = _FakeStore([_record("r1")])
    harness = mig.LeaseMigrationHarness(primary, secondary)

    result = harness.read_dual()

    assert result.agrees is True
    assert result.primary_only == ()
    assert result.secondary_only == ()


def test_read_dual_reports_drift_without_raising() -> None:
    primary = _FakeStore([_record("r1"), _record("r2")])
    secondary = _FakeStore([_record("r2"), _record("r3")])
    harness = mig.LeaseMigrationHarness(primary, secondary)

    result = harness.read_dual()

    assert result.agrees is False
    assert result.primary_only == ("r1",)
    assert result.secondary_only == ("r3",)


def test_drain_copies_primary_state_into_secondary_without_switching_authority() -> None:
    primary = _FakeStore([_record("r1")])
    secondary = _FakeStore()
    harness = mig.LeaseMigrationHarness(primary, secondary)

    result = harness.drain()

    assert result.record_count == 1
    assert result.fencing_token == 1
    assert {r.runner_id for r in secondary.read_all()} == {"r1"}
    assert harness.authority == "primary"
    # The primary is untouched by drain.
    assert {r.runner_id for r in primary.read_all()} == {"r1"}


def test_repeated_drain_reissues_a_fresh_monotonic_fencing_token() -> None:
    harness = mig.LeaseMigrationHarness(_FakeStore([_record("r1")]), _FakeStore())

    first = harness.drain()
    second = harness.drain()

    assert second.fencing_token == first.fencing_token + 1


def test_cutover_rejects_a_stale_fencing_token() -> None:
    harness = mig.LeaseMigrationHarness(_FakeStore([_record("r1")]), _FakeStore())
    stale_token = harness.drain().fencing_token
    harness.drain()  # mints a newer token; `stale_token` is now out of date.

    with pytest.raises(mig.SingleAuthorityViolation):
        harness.cutover(stale_token)

    assert harness.authority == "primary"


def test_cutover_rejects_a_token_before_any_drain() -> None:
    harness = mig.LeaseMigrationHarness(_FakeStore(), _FakeStore())

    with pytest.raises(mig.SingleAuthorityViolation):
        harness.cutover(1)


def test_cutover_with_matching_token_promotes_secondary_authority() -> None:
    harness = mig.LeaseMigrationHarness(_FakeStore([_record("r1")]), _FakeStore())
    token = harness.drain().fencing_token

    harness.cutover(token)

    assert harness.authority == "secondary"
    assert harness.authoritative_store() is harness._secondary


def test_cutover_twice_raises_single_authority_violation() -> None:
    harness = mig.LeaseMigrationHarness(_FakeStore([_record("r1")]), _FakeStore())
    token = harness.drain().fencing_token
    harness.cutover(token)

    with pytest.raises(mig.SingleAuthorityViolation):
        harness.cutover(token)


def test_drain_after_cutover_raises_single_authority_violation() -> None:
    harness = mig.LeaseMigrationHarness(_FakeStore([_record("r1")]), _FakeStore())
    harness.cutover(harness.drain().fencing_token)

    with pytest.raises(mig.SingleAuthorityViolation):
        harness.drain()


def test_rollback_restores_primary_authority_without_a_deploy() -> None:
    harness = mig.LeaseMigrationHarness(_FakeStore([_record("r1")]), _FakeStore())
    harness.cutover(harness.drain().fencing_token)
    assert harness.authority == "secondary"

    harness.rollback()

    assert harness.authority == "primary"
    assert harness.authoritative_store() is harness._primary


def test_rollback_clears_any_pending_drain_token() -> None:
    harness = mig.LeaseMigrationHarness(_FakeStore([_record("r1")]), _FakeStore())
    token = harness.drain().fencing_token
    harness.rollback()

    # Rollback invalidates any outstanding drain; the harness is back at the
    # pre-drain state and the old token must not still be honored.
    with pytest.raises(mig.SingleAuthorityViolation):
        harness.cutover(token)
