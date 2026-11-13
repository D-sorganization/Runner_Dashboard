"""The #4088 fence must survive a legitimate lease.acquire() admission (#4156 acceptance item 2).

#4088 (``staff.lifecycle_fence``) makes an operator ``draft_only``/``no_arm``/``no_merge``
restriction the final section of the *planned* prompt (``RunPlan.prompt``), consumed by
``assert_fence_consumed``. #4156 characterized a real TOCTOU race between dispatch admission
(``staff.lease.acquire``) and an operator claim lookup (``coordination.claims.check``): a run can
be denied (``lease_blocked``) and later found unheld. The remaining open acceptance item asks
whether, when the race resolves the *other* way -- admission legitimately succeeds -- the fence
is still enforced on the prompt actually launched, not just the one reported by a dry run.

It is not: ``StaffRunner._execute`` recomposes the prompt from scratch (to splice in the
post-admission ``lease_note``, which is only known at execution time) via a second
``workspace.compose_prompt`` call that never forwards ``lifecycle_fence`` -- unlike the
planning-time call in ``staff.runner_planning.build_run_plan``. So the *launched* prompt (the one
``resolve_execute_launch`` turns into argv, and the one in ``store.update_run(..., prompt=...)``)
silently drops the ``OPERATOR PR BOUNDARY`` block, independent of whether admission was blocked,
raced, or clean. This is the silent-bypass #4156 asked to rule out.

Reuses #4088/#4160's own helpers (``resolve_pr_lifecycle``, ``assert_fence_consumed``) and
#4156's ``FakeRM`` admission fixture (DRY) instead of re-deriving the fence contract or faking a
second claim-check shim.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from staff import lease as lease_mod
from staff import workspace
from staff.lifecycle_fence import FENCED_LIFECYCLE, assert_fence_consumed, resolve_pr_lifecycle
from staff.plan import RunPlan
from staff.roles import RoleSpec
from staff.runner import RunRecord, StaffRunner
from staff.store import RunStore

from tests.api.coordination_fake_rm import FakeRM, claim_status, lease_result

pytestmark = pytest.mark.unit

REPO = "Runner_Dashboard"
ISSUE = "4156"
AGENT = "antigravity"


class _DummyAdapter:
    executable = "fake-provider"
    prompt_via_stdin = False
    lease_agent = AGENT

    def installed(self) -> bool:
        return True

    def build_command(self, prompt: str, workdir: str, model: str | None = None, **kwargs: Any) -> list[str]:
        return ["fake-provider", prompt]

    def runtime_env(self) -> dict[str, str]:
        return {}

    def parse_line(self, line: str) -> dict[str, Any]:
        return {"kind": "text", "text": line}


def _role() -> RoleSpec:
    return RoleSpec(
        name="issue-remediator",
        title="Issue Remediator",
        providers=("fake-provider",),
        permissions={"lease": True, "push_branch": True, "open_pr": True},
        surface="dashboard",
    )


def _plan(decision) -> RunPlan:
    """A planned prompt built exactly as ``build_run_plan`` builds it (fence as final block)."""
    prompt = workspace.compose_prompt(
        _role(),
        repo=REPO,
        target_ref=f"#{ISSUE}",
        operator_prompt="fix it",
        branch="staff/issue-remediator-4156",
        lifecycle_fence=decision.prompt_block,
    )
    assert_fence_consumed(decision, prompt)
    return RunPlan(
        role="issue-remediator",
        provider="fake-provider",
        model=None,
        repo=REPO,
        target_kind="issue",
        target_ref=f"#{ISSUE}",
        operator_prompt="fix it",
        prompt=prompt,
        argv=[],
        branch="staff/issue-remediator-4156",
        lease_ritual=True,
        pr_lifecycle=decision.pr_lifecycle,
        pr_restrictions=decision.restrictions,
    )


def _runner_and_rec(tmp_path: Path, plan: RunPlan) -> tuple[StaffRunner, RunStore, RunRecord]:
    store = RunStore(tmp_path / "runs.sqlite3")
    runner = StaffRunner(store=store)
    runner._adapters = {"fake-provider": _DummyAdapter()}
    runner._roles_loader = lambda: {"issue-remediator": _role()}
    rec = RunRecord(
        id="run-4156-fence",
        role="issue-remediator",
        provider="fake-provider",
        model=None,
        machine="local",
        repo=REPO,
        target_kind="issue",
        target_ref=f"#{ISSUE}",
        prompt=plan.prompt,
        branch=plan.branch,
    )
    store.create_run(rec)
    return runner, store, rec


@pytest.fixture
def rm_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeRM:
    """#4156's admission fixture: a fake Repository_Management checkout for ``lease.acquire``."""
    fake = FakeRM(tmp_path / "rm")
    monkeypatch.setenv("STAFF_RM_ROOT", str(fake.root))
    monkeypatch.setenv("STAFF_RM_PYTHON", sys.executable)
    return fake


def _admit(rm_root: FakeRM, store: RunStore, rec: RunRecord) -> str:
    """Drive a legitimate admission through ``lease.acquire`` (race resolved in favor of admission)."""
    rm_root.respond("check_agent_claim", claim_status(False))
    rm_root.respond("post_agent_lease", lease_result(agent=AGENT))
    rm_root.respond("agent_communicate:register", {"ok": True})
    note = lease_mod.acquire(store, rec.id, repo=REPO, issue=ISSUE, agent=AGENT, branch=rec.branch)
    assert note is not None, "admission was expected to succeed for this regression"
    return note


def _launched_prompt(runner: StaffRunner, rec: RunRecord, plan: RunPlan, lease_note: str, workdir: Path) -> str:
    captured: dict[str, str] = {}

    def _capture_launch(_plan: Any, _adapter: Any, prompt: str, *_a: Any, **_k: Any) -> tuple[list[str], str, None]:
        captured["prompt"] = prompt
        return (["fake-provider"], "fake-provider (fake)", None)

    with (
        patch("staff.cloud_dispatch.resolve_execute_launch", side_effect=_capture_launch),
        patch("staff.runner_ops.apply_staff_run_github_child_env", side_effect=lambda *a, **k: a[2]),
        patch("staff.runner_ops.sanitize_launch_env", side_effect=lambda env, **k: env),
        patch("staff.runner_ops.apply_staff_run_fleet_token", side_effect=lambda *a, **k: a[4]),
        patch("subprocess.Popen") as mock_popen,
    ):
        mock_proc = mock_popen.return_value
        mock_proc.pid = 4242
        mock_proc.stdin = None
        mock_proc.stdout = iter(["STAFF_RESULT: ok\n"])
        mock_proc.wait.return_value = 0
        mock_proc.poll.return_value = 0
        runner._execute(rec, plan, workdir, lease_note)
    assert "prompt" in captured, "resolve_execute_launch was never called"
    return captured["prompt"]


def test_fenced_run_keeps_the_operator_boundary_after_legitimate_admission(tmp_path: Path, rm_root: FakeRM) -> None:
    """Admission succeeding must never silently drop the draft/no-arm/no-merge fence (#4156)."""
    decision = resolve_pr_lifecycle("arm_and_exit", ("no_arm",))
    plan = _plan(decision)
    runner, store, rec = _runner_and_rec(tmp_path, plan)
    lease_note = _admit(rm_root, store, rec)

    workdir = tmp_path / "work"
    workdir.mkdir()
    launched_prompt = _launched_prompt(runner, rec, plan, lease_note, workdir)

    # Fail-closed: the fence must still be the prompt's final, overriding section.
    assert_fence_consumed(decision, launched_prompt)
    assert plan.pr_lifecycle == FENCED_LIFECYCLE
    stored = store.get_run(rec.id)
    assert stored is not None
    assert stored.prompt is not None and stored.prompt.rstrip().endswith(decision.prompt_block)


def test_unrestricted_run_is_unaffected_by_the_admission_wiring(tmp_path: Path, rm_root: FakeRM) -> None:
    """Ordinary unrestricted work must see no new restriction introduced by this fix."""
    decision = resolve_pr_lifecycle("arm_and_exit", ())
    plan = _plan(decision)
    runner, store, rec = _runner_and_rec(tmp_path, plan)
    lease_note = _admit(rm_root, store, rec)

    workdir = tmp_path / "work"
    workdir.mkdir()
    launched_prompt = _launched_prompt(runner, rec, plan, lease_note, workdir)

    assert "OPERATOR PR BOUNDARY" not in launched_prompt
    assert_fence_consumed(decision, launched_prompt)  # a no-op for an unfenced decision
