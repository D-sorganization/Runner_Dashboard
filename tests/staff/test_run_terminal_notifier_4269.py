"""Every terminal run-status write reaches the notifier exactly once (#4269 PR 3, D-06).

The notifier (``staff.run_link.handle_run_status_change``) releases the dispatch
session and the claim label, updates the linked work item and rewrites the run
card. Before this slice 13 terminal writes skipped it, and a cancel of a live
process called it twice. ``transition()`` now calls it once, on the
compare-and-set that wins the move into a terminal status.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from staff import run_link, run_terminal
from staff import runner_ops as ops
from staff.run_status import (
    LEGAL_TRANSITIONS,
    TERMINAL_STATUSES,
    IllegalRunStatusTransitionError,
    RunStatus,
    RunStatusRaceError,
    mark_run_blocked,
    transition,
    transition_to,
)
from staff.store import RunRecord, RunStore

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
_TERMINAL_LITERALS = frozenset(s.value for s in TERMINAL_STATUSES)

_LEGAL_TERMINAL_MOVES = sorted(
    (src.value, dst.value) for src, targets in LEGAL_TRANSITIONS.items() for dst in targets if dst in TERMINAL_STATUSES
)
_LEGAL_NON_TERMINAL_MOVES = sorted(
    (src.value, dst.value)
    for src, targets in LEGAL_TRANSITIONS.items()
    for dst in targets
    if dst not in TERMINAL_STATUSES
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[RunStore]:
    run_store = RunStore(tmp_path / "runs.sqlite3")
    yield run_store
    run_store.close()


@pytest.fixture
def notified(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, dict[str, Any]]]:
    """Record every call the state machine makes to the terminal notifier."""
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def record(_store: RunStore, run_id: str, status: str, **kwargs: Any) -> None:
        calls.append((run_id, str(status), kwargs))

    monkeypatch.setattr(run_terminal, "notify_run_terminal", record)
    return calls


def _run(store: RunStore, run_id: str, status: str, **extra: Any) -> RunRecord:
    rec = RunRecord(run_id, "ad-hoc", "claude", None, "m", "repo", "prompt", "", "p", status=status, **extra)
    store.create_run(rec)
    return rec


@pytest.mark.unit
@pytest.mark.parametrize(("source", "target"), _LEGAL_TERMINAL_MOVES)
def test_every_legal_move_into_a_terminal_status_notifies_once(
    store: RunStore, notified: list[tuple[str, str, dict[str, Any]]], source: str, target: str
) -> None:
    _run(store, "r1", source)

    transition(store, "r1", source, target)

    assert [(run_id, status) for run_id, status, _ in notified] == [("r1", target)]


@pytest.mark.unit
@pytest.mark.parametrize(("source", "target"), _LEGAL_NON_TERMINAL_MOVES)
def test_a_move_that_is_not_terminal_does_not_notify(
    store: RunStore, notified: list[tuple[str, str, dict[str, Any]]], source: str, target: str
) -> None:
    _run(store, "r1", source)

    transition(store, "r1", source, target)

    assert notified == []


@pytest.mark.unit
def test_only_the_winner_of_a_race_notifies(store: RunStore, notified: list[tuple[str, str, dict[str, Any]]]) -> None:
    _run(store, "r1", "running")
    transition(store, "r1", RunStatus.RUNNING, RunStatus.CANCELLED)

    with pytest.raises(RunStatusRaceError):
        transition(store, "r1", RunStatus.RUNNING, RunStatus.FAILED)
    lost = transition(store, "r1", RunStatus.RUNNING, RunStatus.FAILED, raise_on_race=False)

    assert lost.ok is False
    assert [(run_id, status) for run_id, status, _ in notified] == [("r1", "cancelled")]


@pytest.mark.unit
def test_an_illegal_or_repeated_terminal_write_does_not_notify_again(
    store: RunStore, notified: list[tuple[str, str, dict[str, Any]]]
) -> None:
    _run(store, "r1", "running")
    transition_to(store, "r1", RunStatus.FAILED)

    transition_to(store, "r1", RunStatus.FAILED)  # same status: a no-op, not a second terminal write
    with pytest.raises(IllegalRunStatusTransitionError):
        transition_to(store, "r1", RunStatus.SUCCEEDED)

    assert [(run_id, status) for run_id, status, _ in notified] == [("r1", "failed")]


@pytest.mark.unit
def test_a_caller_that_notifies_itself_can_defer_the_hook(
    store: RunStore, notified: list[tuple[str, str, dict[str, Any]]]
) -> None:
    _run(store, "r1", "running")

    transition_to(store, "r1", RunStatus.SUCCEEDED, notify_terminal=False)

    assert notified == []
    got = store.get_run("r1")
    assert got is not None and got.status == "succeeded"


@pytest.mark.unit
def test_mark_run_blocked_notifies_once_and_never_for_an_already_terminal_run(
    store: RunStore, notified: list[tuple[str, str, dict[str, Any]]]
) -> None:
    _run(store, "live", "needs_input")
    _run(store, "done", "failed")

    mark_run_blocked(store, "live", RunStatus.NEEDS_INPUT, error="providers exhausted")
    mark_run_blocked(store, "done", RunStatus.FAILED, error="providers exhausted")

    assert [(run_id, status) for run_id, status, _ in notified] == [("live", "blocked")]


@pytest.mark.unit
def test_a_failing_notifier_never_undoes_or_aborts_the_status_write(
    store: RunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("card store unavailable")

    monkeypatch.setattr(run_link, "handle_run_status_change", boom)
    _run(store, "r1", "running")

    result = transition(store, "r1", RunStatus.RUNNING, RunStatus.FAILED, error="x")

    assert result.ok is True
    got = store.get_run("r1")
    assert got is not None and got.status == "failed"


@pytest.mark.unit
def test_the_notifier_gets_the_stored_row_after_the_write(store: RunStore, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str, str, str | None]] = []

    def capture(run: RunRecord, status: str, question: str | None = None, summary: str | None = None, **_: Any) -> None:
        seen.append((run.status, status, run.error, summary))

    monkeypatch.setattr(run_link, "handle_run_status_change", capture)
    _run(store, "r1", "queued")

    transition(store, "r1", RunStatus.QUEUED, RunStatus.FAILED, error="worker did not start")

    assert seen == [("failed", "failed", "worker did not start", None)]


@pytest.mark.unit
def test_worker_crash_notifies_once(store: RunStore, notified: list[tuple[str, str, dict[str, Any]]]) -> None:
    rec = _run(store, "r1", "preparing")

    ops.record_worker_crash(store, rec, RuntimeError("boom"))

    assert [(run_id, status) for run_id, status, _ in notified] == [("r1", "failed")]


@pytest.mark.unit
def test_blocked_run_card_reads_as_failed_not_running() -> None:
    """A blocked run never started, so its card must not fall back to ``running``."""
    rec = RunRecord("r1", "ad-hoc", "claude", None, "m", "repo", "prompt", "", "p", status="blocked")
    rec.failure_class = "lease_blocked"
    rec.error = "issue claim held by another agent"

    assert run_link.card_run(rec, "blocked")["status"] == "failed"
    body = run_link.format_run_card_body(rec, "blocked")
    assert "issue claim held by another agent" in body
    assert "lease_blocked" in body


def _calls(tree: ast.AST) -> Iterator[ast.Call]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


def _callee(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


@pytest.mark.unit
def test_no_site_notifies_a_terminal_status_by_hand() -> None:
    """A literal terminal status passed straight to the notifier would be a second call."""
    offenders: list[str] = []
    for path in sorted(_BACKEND.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in _calls(tree):
            if _callee(node) != "handle_run_status_change":
                continue
            values = [*node.args[1:2], *(kw.value for kw in node.keywords if kw.arg == "status")]
            for value in values:
                if isinstance(value, ast.Constant) and value.value in _TERMINAL_LITERALS:
                    offenders.append(f"{path.relative_to(_BACKEND).as_posix()}:{node.lineno}")
    assert offenders == []


@pytest.mark.unit
def test_only_the_run_finish_path_defers_the_hook() -> None:
    """``notify_terminal=False`` is allowed where the caller emits the notification itself."""
    deferring: set[str] = set()
    for path in sorted(_BACKEND.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in _calls(tree):
            if any(kw.arg == "notify_terminal" for kw in node.keywords):
                deferring.add(path.relative_to(_BACKEND).as_posix())
    # ``run_status.py`` only forwards the keyword from ``transition_to`` to ``transition``.
    assert deferring - {"staff/run_status.py"} == {"staff/runner_finish.py"}
