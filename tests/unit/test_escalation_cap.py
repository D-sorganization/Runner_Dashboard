"""Expedite concurrent cap."""

from __future__ import annotations

from pathlib import Path

import pytest
from escalation.cap import capacity_refused, resolve_cap
from escalation.store import EscalationEvent, EscalationStore, reset_escalation_store


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_escalation_store()
    yield
    reset_escalation_store()


@pytest.mark.unit
def test_resolve_cap_defaults_to_three() -> None:
    assert resolve_cap(None) == 3


@pytest.mark.unit
def test_capacity_refused_when_at_cap(tmp_path: Path) -> None:
    store = EscalationStore(tmp_path / "e.sqlite3")
    repo = "D-sorganization/Runner_Dashboard"
    for pr in (1, 2, 3):
        store.append(EscalationEvent(repo=repo, pr_number=pr, github_actor="dieterolson", outcome="applied"))
    msg = capacity_refused(repo, 3, pr_number=99, store=store)
    assert msg is not None
    assert capacity_refused(repo, 3, pr_number=1, store=store) is None
