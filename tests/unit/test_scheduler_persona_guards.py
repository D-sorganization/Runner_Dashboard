"""Persona fire-guard selection for StaffScheduler slots (#3869)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from staff.barb_continuity import BarbContinuityStore
from staff.scheduler_persona_guards import persona_fire_guard

pytestmark = pytest.mark.unit
NOW = datetime(2026, 11, 8, 14, 0, tzinfo=UTC)


def test_roles_without_a_guard_never_open_the_continuity_store() -> None:
    def _unexpected() -> BarbContinuityStore:
        raise AssertionError("continuity store opened for an unguarded role")

    assert persona_fire_guard("barb", continuity=_unexpected, job=None, slot="s", now=NOW) == ("", "")


@pytest.mark.parametrize(
    ("role", "job", "expected"),
    [
        ("cartographer", None, ("weekly_map_sweep", "")),
        ("lost-code-finder", "weekly_audit", ("", "")),
        ("lost-code-finder", "nightly_sweep", ("", "unknown_job")),
    ],
)
def test_guarded_roles(tmp_path: Path, role: str, job: str | None, expected: tuple[str, str]) -> None:
    store = BarbContinuityStore(tmp_path / "c.db")
    assert persona_fire_guard(role, continuity=lambda: store, job=job, slot="2026-11-08T06:00", now=NOW) == expected
