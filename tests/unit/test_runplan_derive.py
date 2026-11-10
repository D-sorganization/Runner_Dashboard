"""RunPlan.derive() preserves full field sets on retry/failover (#4259)."""

from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from staff.failover_dispatch import try_chain_failover
from staff.plan import RunPlan
from staff.provider_cooldown import reset_provider_cooldowns
from staff.provider_failover_config import reset_failover_config_cache
from staff.retry import handle_post_execution_retry
from staff.roles import RoleSpec
from staff.store import RunRecord, RunStore

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STAFF_ROOT = _REPO_ROOT / "backend" / "staff"

# Fresh plans from requests; retry_plan rebuilds from RunRecord (no persisted fence fields).
_RUNPLAN_CALL = re.compile(r"\bRunPlan\(")

_DIRECT_RUNPLAN_ALLOWLIST = frozenset(
    {
        _STAFF_ROOT / "plan.py",
        _STAFF_ROOT / "runner_planning.py",
        _STAFF_ROOT / "retry.py",  # retry_plan only; guarded by test below
    }
)


def _full_plan(**overrides: object) -> RunPlan:
    base = RunPlan(
        role="worker",
        provider="codex",
        model="gpt-6-luna",
        repo="Runner_Dashboard",
        target_kind="issue",
        target_ref="#1",
        operator_prompt="op",
        prompt="do work",
        argv=["codex"],
        branch="staff/worker-task",
        lease_ritual=True,
        pr_lifecycle="draft_no_arm",
        pr_restrictions=("draft_only", "no_arm"),
        head_ref="main",
        model_tier="strong",
    )
    return dataclasses.replace(base, **overrides)


@pytest.fixture(autouse=True)
def _reset_failover_cache() -> None:
    reset_provider_cooldowns()
    reset_failover_config_cache()


def test_derive_defaults_copy_every_field() -> None:
    plan = _full_plan()
    derived = plan.derive(branch="staff/worker-task-retry-2", argv=["codex", "--retry"])
    for fld in dataclasses.fields(RunPlan):
        if fld.name in ("branch", "argv"):
            continue
        assert getattr(derived, fld.name) == getattr(plan, fld.name), fld.name
    assert derived.branch == "staff/worker-task-retry-2"
    assert derived.argv == ["codex", "--retry"]


def test_staff_retry_paths_use_derive_not_raw_constructor() -> None:
    """Every RunPlan( outside allowlisted modules must go through .derive()."""
    offenders: list[str] = []
    for path in sorted(_STAFF_ROOT.rglob("*.py")):
        if path in _DIRECT_RUNPLAN_ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        if not _RUNPLAN_CALL.search(text):
            continue
        offenders.append(str(path.relative_to(_REPO_ROOT)))
    assert offenders == [], f"use plan.derive(); found RunPlan( in: {offenders}"


def test_retry_module_only_retry_plan_builds_runplan_directly() -> None:
    tree = ast.parse((_STAFF_ROOT / "retry.py").read_text(encoding="utf-8"))

    class ParentVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.parents: dict[ast.AST, ast.AST] = {}

        def visit(self, node: ast.AST) -> None:
            for child in ast.iter_child_nodes(node):
                self.parents[child] = node
            super().visit(node)

    pv = ParentVisitor()
    pv.visit(tree)
    direct: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "RunPlan":
            func_node: ast.AST | None = node
            name = ""
            while func_node is not None:
                if isinstance(func_node, ast.FunctionDef):
                    name = func_node.name
                    break
                func_node = pv.parents.get(func_node)
            if name != "retry_plan":
                direct.append(name or "<module>")
    assert direct == [], direct


def test_same_provider_retry_keeps_pr_restrictions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = RunStore(path=tmp_path / "runs.sqlite3")
    plan = _full_plan()
    root = RunRecord(
        id="run-root",
        role=plan.role,
        provider=plan.provider,
        model=plan.model,
        machine="test",
        repo=plan.repo,
        target_kind=plan.target_kind,
        target_ref=plan.target_ref,
        prompt=plan.prompt,
        status="failed",
        failure_class="rate_limited",
        attempt=1,
        max_attempts=3,
        branch=plan.branch,
    )
    store.create_run(root)
    role = RoleSpec(
        name="worker",
        title="Worker",
        providers=("codex",),
        max_attempts=3,
    )
    runner = MagicMock()
    runner.store = store
    runner.machine = "test"
    runner.roles.return_value = {"worker": role}
    runner._cancel_flags = set()
    runner._adapters = {
        "codex": MagicMock(
            installed=lambda: True,
            unattended=True,
            build_command=lambda *a, **k: ["codex"],
        ),
    }
    scheduled: list[tuple[RunRecord, RunPlan]] = []

    def capture_schedule(_runner: object, rec: RunRecord, attempt_plan: RunPlan, _delay: float) -> None:
        scheduled.append((rec, attempt_plan))

    monkeypatch.setattr("staff.retry._schedule", capture_schedule)

    latest = store.get_run("run-root")
    assert latest is not None
    assert handle_post_execution_retry(runner, latest, plan) is True
    assert len(scheduled) == 1
    _, attempt_plan = scheduled[0]
    assert attempt_plan.pr_restrictions == plan.pr_restrictions
    assert attempt_plan.pr_lifecycle == plan.pr_lifecycle


def test_chain_failover_keeps_pr_restrictions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = RunStore(path=tmp_path / "runs.sqlite3")
    plan = _full_plan()
    root = RunRecord(
        id="run-root",
        role=plan.role,
        provider="codex",
        model=plan.model,
        machine="test",
        repo=plan.repo,
        target_kind=plan.target_kind,
        target_ref=plan.target_ref,
        prompt=plan.prompt,
        status="failed",
        failure_class="provider_capacity",
        attempt=1,
        max_attempts=2,
        branch=plan.branch,
    )
    store.create_run(root)
    role = RoleSpec(name="worker", title="Worker", providers=("codex", "claude"))
    runner = MagicMock()
    runner.store = store
    runner.machine = "test"
    runner._adapters = {
        "codex": MagicMock(
            installed=lambda: True,
            unattended=True,
            executable="codex",
            build_command=lambda *a, **k: ["codex"],
        ),
        "claude": MagicMock(
            installed=lambda: True,
            unattended=True,
            executable="claude",
            build_command=lambda *a, **k: ["claude"],
        ),
    }
    guard = MagicMock()
    guard.can_run.return_value = (True, "")
    scheduled: list[RunPlan] = []

    def capture_schedule(_runner: object, _rec: RunRecord, attempt_plan: RunPlan, _delay: float) -> None:
        scheduled.append(attempt_plan)

    monkeypatch.setattr("staff.retry._schedule", capture_schedule)
    latest = store.get_run("run-root")
    assert latest is not None
    assert try_chain_failover(runner, latest, plan, role, guard, retries_exhausted=False) is True
    assert len(scheduled) == 1
    assert scheduled[0].pr_restrictions == plan.pr_restrictions
    assert scheduled[0].pr_lifecycle == plan.pr_lifecycle
