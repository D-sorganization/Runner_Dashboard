"""Characterization tests for staff run status literals and transitions (#4269, wave 0.7).

Pins today's behavior before RunStatus(StrEnum) and compare-and-set land in PR 2.
These tests are intentionally strict: changing a literal or an observed edge should fail.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Iterator
from pathlib import Path

import pytest
from staff import claim_guard
from staff.barb_runtime import TERMINAL_STATUSES as BARB_RUNTIME_TERMINAL
from staff.barb_versions import TERMINAL_STATUSES as BARB_VERSIONS_TERMINAL
from staff.run_link import _CARD_STATUS, card_run, update_linked_work_item
from staff.run_outcome import _IN_FLIGHT, BUCKETS, classify_outcome
from staff.scheduler_missed_runs import _LIVE_STATUSES
from staff.store import ACTIVE_STATUSES, RUN_STATUSES, RunRecord, RunStore
from staff.work_items import WorkItemStore, get_work_item_store, reset_work_item_store

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND_STORE_SCAN_ROOTS = (
    _REPO_ROOT / "backend" / "staff",
    _REPO_ROOT / "backend" / "coordination",
)


def _store_status_kw_value(node: ast.Call) -> str | None:
    for kw in node.keywords:
        if kw.arg != "status":
            continue
        if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _is_run_store_write_call(node: ast.Call) -> bool:
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr in ("update_run", "create_run")


def _collect_store_status_write_literals_from_ast() -> tuple[frozenset[str], int]:
    """String status literals on ``update_run`` / ``create_run`` keyword args in backend staff sources."""
    literals: set[str] = set()
    call_sites = 0
    for root in _BACKEND_STORE_SCAN_ROOTS:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not _is_run_store_write_call(node):
                    continue
                if not any(kw.arg == "status" for kw in node.keywords):
                    continue
                call_sites += 1
                lit = _store_status_kw_value(node)
                if lit is not None:
                    literals.add(lit)
    return frozenset(literals), call_sites


# ── Literal snapshots (must match imports / source at characterization time) ──

PINNED_RUN_STATUSES: tuple[str, ...] = (
    "queued",
    "preparing",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "blocked",
    "needs_input",
)

PINNED_ACTIVE_STATUSES: tuple[str, ...] = ("queued", "preparing", "running")

PINNED_LIVE_STATUSES: tuple[str, ...] = ("queued", "preparing", "running", "needs_input")

PINNED_CARD_STATUS_MAP: dict[str, str] = {
    "queued": "queued",
    "preparing": "running",
    "running": "running",
    "needs_input": "needs_input",
    "succeeded": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
    "blocked": "failed",  # PR 3: a blocked run's card no longer falls back to ``running``
}

PINNED_CLAIM_GUARD_TERMINAL: frozenset[str] = frozenset({"succeeded", "failed", "cancelled", "blocked"})

PINNED_BARB_TERMINAL: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})

# backend/staff/retention.py archive path (inline tuple, not exported).
PINNED_RETENTION_TERMINAL: tuple[str, ...] = ("succeeded", "failed", "cancelled")

# backend/staff/worktree_hygiene.py _cleanup_finished_worktree guard.
PINNED_WORKTREE_HYGIENE_TERMINAL: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})

PINNED_RUN_OUTCOME_IN_FLIGHT: frozenset[str] = frozenset({"queued", "preparing", "running", "needs_input"})

PINNED_OUTCOME_BUCKETS: tuple[str, ...] = (
    "success",
    "no_op",
    "quality_fail",
    "infra_fail",
    "cancelled",
    "in_flight",
)

# handle_run_status_change calls in staff.runner.StaffRunner (worker / admit). Terminal
# statuses are notified by ``run_status.transition()`` since PR 3, so ``cancelled`` left.
PINNED_RUNNER_HANDLE_STATUSES: tuple[str, ...] = (
    "queued",
    "preparing",
    "running",
)


def test_run_status_literals_from_store() -> None:
    assert RUN_STATUSES == PINNED_RUN_STATUSES
    assert ACTIVE_STATUSES == PINNED_ACTIVE_STATUSES


def test_live_statuses_include_needs_input_but_active_statuses_do_not() -> None:
    assert _LIVE_STATUSES == PINNED_LIVE_STATUSES
    assert "needs_input" in _LIVE_STATUSES
    assert "needs_input" not in ACTIVE_STATUSES


def test_run_card_status_map_literals() -> None:
    assert _CARD_STATUS == PINNED_CARD_STATUS_MAP
    assert set(_CARD_STATUS) <= set(RUN_STATUSES)


def test_competing_terminal_status_definitions() -> None:
    """Six terminal notions coexist today (D-05); they must stay distinct until unified."""
    assert claim_guard.TERMINAL_STATUSES == PINNED_CLAIM_GUARD_TERMINAL
    assert BARB_RUNTIME_TERMINAL == PINNED_BARB_TERMINAL
    assert BARB_VERSIONS_TERMINAL == PINNED_BARB_TERMINAL
    assert PINNED_RETENTION_TERMINAL == ("succeeded", "failed", "cancelled")
    assert PINNED_WORKTREE_HYGIENE_TERMINAL == {"succeeded", "failed", "cancelled"}
    # claim_guard is the only one that treats ``blocked`` as terminal for claims.
    assert "blocked" in claim_guard.TERMINAL_STATUSES
    assert "blocked" not in BARB_RUNTIME_TERMINAL


def test_retention_archives_only_terminal_run_statuses(tmp_path: Path) -> None:
    from staff.retention import archive_old_runs  # noqa: PLC0415

    archive_dir = tmp_path / "archives"
    store = RunStore(tmp_path / "runs.sqlite3")
    old_ts = "2020-01-01T00:00:00Z"
    statuses = ("succeeded", "failed", "cancelled", "blocked", "needs_input", "running")
    try:
        for idx, status in enumerate(statuses):
            store.create_run(
                RunRecord(
                    f"ret-{idx}",
                    "ad-hoc",
                    "claude",
                    None,
                    "m",
                    "repo",
                    "prompt",
                    "",
                    "p",
                    status=status,
                    created_at=old_ts,
                )
            )
        archived_count, _files = archive_old_runs(
            store=store,
            retention_days=1,
            archive_dir=archive_dir,
            reference_date="2026-06-01T00:00:00Z",
        )
        assert archived_count == 3
        for idx, status in enumerate(statuses):
            rec = store.get_run(f"ret-{idx}")
            if status in PINNED_RETENTION_TERMINAL:
                assert rec is None, status
            else:
                assert rec is not None and rec.status == status
    finally:
        store.close()


def test_run_outcome_in_flight_and_buckets() -> None:
    assert _IN_FLIGHT == PINNED_RUN_OUTCOME_IN_FLIGHT
    assert BUCKETS == PINNED_OUTCOME_BUCKETS
    for status in RUN_STATUSES:
        bucket = classify_outcome(status)
        assert bucket in BUCKETS


@pytest.mark.parametrize(
    ("status", "verification", "expected_bucket"),
    [
        ("queued", "", "in_flight"),
        ("running", "", "in_flight"),
        ("succeeded", "", "no_op"),
        ("succeeded", "verified", "success"),
        ("failed", "", "quality_fail"),
        ("cancelled", "", "cancelled"),
        ("blocked", "", "infra_fail"),
        ("needs_input", "", "in_flight"),
    ],
)
def test_classify_outcome_terminal_and_in_flight_pins(status: str, verification: str, expected_bucket: str) -> None:
    assert classify_outcome(status, verification=verification) == expected_bucket


def test_store_write_literals_are_subset_of_run_statuses() -> None:
    literals, call_sites = _collect_store_status_write_literals_from_ast()
    assert call_sites > 0, "expected at least one update_run/create_run call with status="
    unknown = literals - set(RUN_STATUSES)
    assert not unknown, f"store write status literals not in RUN_STATUSES: {sorted(unknown)}"


def test_store_rejects_unknown_status_literal(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.sqlite3")
    try:
        rec = RunRecord(
            "r-unknown",
            "ad-hoc",
            "claude",
            None,
            "m",
            "repo",
            "prompt",
            "",
            "p",
            status="not_a_real_status",
        )
        with pytest.raises(AssertionError):
            store.create_run(rec)
    finally:
        store.close()


def test_store_accepts_every_canonical_literal(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.sqlite3")
    try:
        for idx, status in enumerate(RUN_STATUSES):
            rid = f"run-lit-{idx}"
            store.create_run(RunRecord(rid, "ad-hoc", "claude", None, "m", "repo", "prompt", "", "p", status=status))
            got = store.get_run(rid)
            assert got is not None and got.status == status
    finally:
        store.close()


def test_unconditional_update_run_allows_cancelled_to_running(tmp_path: Path) -> None:
    """D-05: no compare-and-set today — cancelled can flip back to running."""
    store = RunStore(tmp_path / "runs.sqlite3")
    try:
        store.create_run(RunRecord("reopen", "ad-hoc", "claude", None, "m", "repo", "prompt", "", "p", status="queued"))
        store.update_run("reopen", status="cancelled", ended_at="2026-11-11T00:00:00Z")
        store.update_run("reopen", status="running")
        got = store.get_run("reopen")
        assert got is not None
        assert got.status == "running"
    finally:
        store.close()


def test_card_run_maps_blocked_to_failed_on_console() -> None:
    run = RunRecord("r1", "ad-hoc", "claude", None, "m", "repo", "prompt", "", "p")
    assert card_run(run, "blocked")["status"] == "failed"


def test_runner_worker_handle_run_status_literals_are_pinned() -> None:
    from staff import runner as runner_mod  # noqa: PLC0415

    source = inspect.getsource(runner_mod.StaffRunner)
    for status in PINNED_RUNNER_HANDLE_STATUSES:
        assert f'handle_run_status_change(rec, "{status}"' in source


@pytest.fixture
def wi_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[WorkItemStore]:
    db = tmp_path / "runs.sqlite3"
    monkeypatch.setenv("STAFF_RUNS_DB", str(db))
    reset_work_item_store()
    yield get_work_item_store(db)
    reset_work_item_store()


@pytest.mark.parametrize(
    ("run_status", "wi_state_before", "expected_wi_state"),
    [
        ("running", "open", "in_progress"),
        ("preparing", "waiting_on_user", "in_progress"),
        ("needs_input", "in_progress", "waiting_on_user"),
        ("succeeded", "in_progress", "needs_evidence"),
        ("failed", "in_progress", "blocked"),
        ("cancelled", "in_progress", "open"),
        ("blocked", "open", "blocked"),
    ],
)
def test_run_status_to_work_item_transitions_observed(
    wi_store: WorkItemStore, run_status: str, wi_state_before: str, expected_wi_state: str
) -> None:
    wi = wi_store.create_work_item(title="t", requested_by="op")
    if wi_state_before == "in_progress":
        wi_store.transition_state(wi.id, "in_progress", actor="test", reason="setup")
    elif wi_state_before == "waiting_on_user":
        wi_store.transition_state(wi.id, "in_progress", actor="test", reason="setup")
        wi_store.transition_state(wi.id, "waiting_on_user", actor="test", reason="setup")
    elif wi_state_before != "open":
        raise AssertionError(f"unsupported fixture state {wi_state_before}")
    run = RunRecord(
        "run-wi",
        "ad-hoc",
        "claude",
        None,
        "m",
        "repo",
        "prompt",
        "",
        "p",
        work_item_id=wi.id,
        artifact="",
    )
    update_linked_work_item(run, run_status)
    got = wi_store.get_work_item(wi.id)
    assert got is not None
    assert got.state == expected_wi_state


@pytest.mark.parametrize(
    ("artifact", "expected"),
    [("pr_open", "waiting_on_ci"), ("pr_merged", "done"), ("", "needs_evidence")],
)
def test_succeeded_run_work_item_transition_by_artifact(wi_store: WorkItemStore, artifact: str, expected: str) -> None:
    wi = wi_store.create_work_item(title="t", requested_by="op")
    wi_store.transition_state(wi.id, "in_progress", actor="test", reason="setup")
    run = RunRecord(
        "run-art",
        "ad-hoc",
        "claude",
        None,
        "m",
        "repo",
        "prompt",
        "",
        "p",
        work_item_id=wi.id,
        artifact=artifact,
    )
    update_linked_work_item(run, "succeeded")
    got = wi_store.get_work_item(wi.id)
    assert got is not None
    assert got.state == expected
