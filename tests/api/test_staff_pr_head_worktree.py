"""Staff runs that fix an existing PR start from the PR head, not origin/main (#1881 review)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from staff import workspace
from staff.dispatch_service import DispatchCommand
from staff.plan import RunPlan, RunRequest
from staff.runner import StaffRunner


class _Store:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str]] = []

    def update_run(self, run_id: str, **fields: Any) -> None:
        pass

    def append_event(self, run_id: str, kind: str, text: str) -> None:
        self.events.append((run_id, kind, text))


class _Rec:
    id = "run-headtest"


def _plan(head_ref: str) -> RunPlan:
    return RunPlan(
        role="ad-hoc",
        provider="claude",
        model=None,
        repo="Runner_Dashboard",
        target_kind="pr",
        target_ref="PR #42",
        operator_prompt="fix",
        prompt="fix",
        argv=[],
        branch="staff/ad-hoc-42-abc123",
        lease_ritual=False,
        head_ref=head_ref,
    )


def test_prepare_workdir_starts_from_pr_head(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(workspace, "find_repo_checkout", lambda repo: tmp_path / "checkout")
    monkeypatch.setattr(workspace, "staff_worktrees_root", lambda: tmp_path / "wt")
    monkeypatch.setattr(
        workspace, "add_worktree", lambda c, w, b, **kw: calls.append((c, w, b, kw.get("start_ref", "")))
    )
    runner = StaffRunner(store=_Store())  # type: ignore[arg-type]
    runner._prepare_workdir(_Rec(), _plan("feat/x"))  # type: ignore[arg-type]
    assert calls[0][2] == "staff/ad-hoc-42-abc123"
    assert calls[0][3] == "feat/x"


def test_prepare_workdir_without_head_ref_keeps_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(workspace, "find_repo_checkout", lambda repo: tmp_path / "checkout")
    monkeypatch.setattr(workspace, "staff_worktrees_root", lambda: tmp_path / "wt")
    monkeypatch.setattr(workspace, "add_worktree", lambda c, w, b: calls.append((c, w, b)))
    runner = StaffRunner(store=_Store())  # type: ignore[arg-type]
    runner._prepare_workdir(_Rec(), _plan(""))  # type: ignore[arg-type]
    assert len(calls) == 1


def test_add_worktree_from_start_ref_fetches_and_branches_from_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    git_calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(workspace, "_git", lambda cwd, *args: git_calls.append(args))
    workspace.add_worktree(tmp_path / "c", tmp_path / "w", "staff/x", start_ref="feat/x")
    assert ("fetch", "origin", "--quiet", "+refs/heads/feat/x:refs/remotes/origin/feat/x") in git_calls
    assert git_calls[-1] == ("worktree", "add", "-b", "staff/x", str(tmp_path / "w"), "origin/feat/x")


def test_plan_carries_head_ref_and_rejects_unsafe_refs() -> None:
    runner = StaffRunner(store=_Store())  # type: ignore[arg-type]
    plan = runner.plan(RunRequest(role="ad-hoc", provider="claude", repo="Runner_Dashboard", pr=42, head_ref="feat/x"))
    assert plan.head_ref == "feat/x"
    assert plan.to_dict()["head_ref"] == "feat/x"
    for bad in ("a b;rm", "../escape"):
        with pytest.raises(ValueError, match="head_ref"):
            RunRequest(role="ad-hoc", provider="claude", repo="Runner_Dashboard", pr=42, head_ref=bad)


def test_dispatch_command_forwards_head_ref_only_when_set() -> None:
    plain = DispatchCommand(role="ad-hoc", requested_by="op", repo="Runner_Dashboard", pr=1, prompt="x")
    assert "head_ref" not in plain.forward_body()
    headed = DispatchCommand(role="ad-hoc", requested_by="op", repo="Runner_Dashboard", pr=1, prompt="x", head_ref="b")
    assert headed.forward_body()["head_ref"] == "b"
