"""Regression: the process-wide scheduler follows the process-wide runner (#3622).

``get_scheduler()`` used to keep the first runner's ``BudgetGuard(runner.store)``
forever, so once that runner (and its store) was replaced, every board or summary
read failed with ``sqlite3.ProgrammingError: Cannot operate on a closed database``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from staff import runner_registry
from staff import scheduler as scheduler_mod
from staff import store as store_mod
from staff.runner import StaffRunner


@pytest.fixture(autouse=True)
def _isolated_singletons(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(tmp_path / "roles"))
    scheduler_mod.reset_scheduler()
    store_mod.reset_store()
    runner_registry.reset_runner()
    yield
    scheduler_mod.reset_scheduler()
    store_mod.reset_store()
    runner_registry.reset_runner()


def test_get_scheduler_rebinds_when_the_runner_is_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first_store = store_mod.RunStore(tmp_path / "first.sqlite3")
    monkeypatch.setattr(runner_registry, "_runner", StaffRunner(store=first_store, machine="A"))
    first = scheduler_mod.get_scheduler()
    assert first.runner is runner_registry.get_runner()

    first_store.close()
    second_runner = StaffRunner(store=store_mod.RunStore(tmp_path / "second.sqlite3"), machine="B")
    monkeypatch.setattr(runner_registry, "_runner", second_runner)

    second = scheduler_mod.get_scheduler()
    assert second.runner is second_runner
    assert second.budget.spent_today("night-watch") == 0.0


def test_get_scheduler_is_stable_while_the_runner_is_unchanged() -> None:
    assert scheduler_mod.get_scheduler() is scheduler_mod.get_scheduler()
