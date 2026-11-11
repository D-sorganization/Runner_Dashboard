"""#4266 A-03: dispatch_bundle and library_references survive retry and failover scheduling."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from staff import retry as retry_mod
from staff.dispatch_bundle import resolve_dispatch_bundle
from staff.failover_dispatch import try_chain_failover
from staff.plan import RunPlan
from staff.retry import handle_post_execution_retry
from staff.roles import RoleSpec
from staff.runner import RunRecord, StaffRunner
from staff.skills.registry import clear_skills_cache
from staff.standards_registry import TOTAL_CONTEXT_TOKEN_CAP
from staff.store import RunStore

pytestmark = pytest.mark.unit

_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "skills_dispatch"
_LIB_MARKER = "LIBREF-4266-A03-RETRY-FAILOVER"


@pytest.fixture
def skills_env(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_skills_cache()
    monkeypatch.setenv("STAFF_SKILLS_DIR", str(_FIXTURE_ROOT))
    yield
    clear_skills_cache()


def _night_watch_bundle(skills_env: None) -> tuple[RoleSpec, Any]:
    role = RoleSpec(
        name="night-watch",
        title="Night Watch",
        providers=("codex", "claude"),
        permissions={"lease": False, "push_branch": True, "open_pr": True},
    )
    bundle = resolve_dispatch_bundle(role="night-watch", repo="AffineDrift")
    assert bundle.skill_block
    return role, bundle


def _run_plan(role: RoleSpec, bundle: Any, *, provider: str = "codex") -> RunPlan:
    return RunPlan(
        role=role.name,
        provider=provider,
        model="gpt-6-luna",
        repo="AffineDrift",
        target_kind="issue",
        target_ref="#1",
        operator_prompt="sweep",
        prompt=f"task\n\n{bundle.skill_block}",
        argv=["codex", "task"],
        branch="staff/night-watch-1",
        lease_ritual=False,
        dispatch_bundle=bundle,
        library_references=_LIB_MARKER,
    )


def test_same_provider_retry_preserves_skill_block_and_library_refs(
    tmp_path: Path,
    skills_env: None,
) -> None:
    role, bundle = _night_watch_bundle(skills_env)
    plan = _run_plan(role, bundle)
    skill_block = bundle.skill_block

    store = RunStore(path=tmp_path / "retry.sqlite3")
    store.create_run(
        RunRecord(
            id="run-retry-root",
            role=role.name,
            provider=plan.provider,
            model=plan.model,
            machine="local",
            repo=plan.repo,
            target_kind=plan.target_kind,
            target_ref=plan.target_ref,
            prompt=plan.prompt,
            status="failed",
            failure_class="provider_error",
            attempt=1,
            max_attempts=3,
        )
    )
    runner = MagicMock(store=store, machine="local", _adapters={"codex": MagicMock()})
    runner.roles.return_value = {role.name: role}
    captured: list[RunPlan] = []

    def _capture_schedule(_runner: Any, _rec: RunRecord, scheduled: RunPlan, _delay: float) -> None:
        captured.append(scheduled)

    guard = MagicMock()
    guard.can_run.return_value = (True, "")

    with (
        patch("staff.provider_fallback.try_declarative_fallback", return_value=False),
        patch("staff.failover_dispatch.try_chain_failover", return_value=False),
        patch.object(retry_mod, "_schedule", side_effect=_capture_schedule),
    ):
        assert handle_post_execution_retry(runner, store.get_run("run-retry-root"), plan)

    assert len(captured) == 1
    scheduled = captured[0]
    assert scheduled.dispatch_bundle is plan.dispatch_bundle
    assert scheduled.dispatch_bundle.skill_block == skill_block
    assert scheduled.library_references == _LIB_MARKER
    assert skill_block in scheduled.prompt


def test_chain_failover_preserves_skill_block_and_library_refs(
    tmp_path: Path,
    skills_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("staff.failover_dispatch.can_run_unattended", lambda *a, **k: True)
    role, bundle = _night_watch_bundle(skills_env)
    skill_block = bundle.skill_block
    plan = _run_plan(role, bundle)

    store = RunStore(path=tmp_path / "failover.sqlite3")
    store.create_run(
        RunRecord(
            id="run-fail-root",
            role=role.name,
            provider=plan.provider,
            model=plan.model,
            machine="local",
            repo=plan.repo,
            target_kind=plan.target_kind,
            target_ref=plan.target_ref,
            prompt=plan.prompt,
            status="failed",
            failure_class="provider_capacity",
            attempt=1,
            max_attempts=2,
        )
    )
    runner = MagicMock(store=store, machine="local")
    runner._adapters = {
        "codex": MagicMock(
            installed=lambda: True,
            unattended=True,
            executable="codex",
            default_model="gpt-6-luna",
            models=(),
            build_command=lambda *a, **k: ["codex"],
        ),
        "claude": MagicMock(
            installed=lambda: True,
            unattended=True,
            executable="claude",
            default_model="sonnet",
            models=(),
            build_command=lambda *a, **k: ["claude"],
        ),
    }
    guard = MagicMock()
    guard.can_run.return_value = (True, "")
    captured: list[RunPlan] = []

    def _capture_schedule(_runner: Any, _rec: RunRecord, scheduled: RunPlan, _delay: float) -> None:
        captured.append(scheduled)

    with patch.object(retry_mod, "_schedule", side_effect=_capture_schedule):
        assert try_chain_failover(
            runner,
            store.get_run("run-fail-root"),
            plan,
            role,
            guard,
            retries_exhausted=False,
        )

    assert len(captured) == 1
    scheduled = captured[0]
    assert scheduled.dispatch_bundle is plan.dispatch_bundle
    assert scheduled.dispatch_bundle.skill_block == skill_block
    assert scheduled.library_references == _LIB_MARKER


class _StubAdapter:
    executable = "codex"
    prompt_via_stdin = False
    lease_agent = "codex"
    unattended = True
    default_model = "gpt-6-luna"

    def installed(self) -> bool:
        return True

    def build_command(self, prompt: str, workdir: str, model: str | None = None, **kwargs: Any) -> list[str]:
        return ["codex", prompt]

    def runtime_env(self) -> dict[str, str]:
        return {}

    def parse_line(self, line: str) -> dict[str, Any]:
        return {"kind": "text", "text": line}


def test_execute_recompose_passes_codex_prompt_token_cap(
    tmp_path: Path,
    skills_env: None,
) -> None:
    """_execute must forward TOTAL_CONTEXT_TOKEN_CAP into compose_fenced for codex (recompose path)."""
    role, bundle = _night_watch_bundle(skills_env)
    plan = _run_plan(role, bundle, provider="codex")
    seen: dict[str, int | None] = {"cap": None}

    def _fake_compose_fenced(_plan: Any, _role: Any, **kwargs: Any) -> str:
        seen["cap"] = kwargs.get("prompt_token_cap")
        return plan.prompt

    store = RunStore(tmp_path / "cap.sqlite3")
    runner = StaffRunner(store=store)
    runner._adapters = {"codex": _StubAdapter()}
    runner._roles_loader = lambda: {role.name: role}
    rec = RunRecord(
        id="run-cap",
        role=role.name,
        provider="codex",
        model="gpt-6-luna",
        machine="local",
        repo=plan.repo,
        target_kind=plan.target_kind,
        target_ref=plan.target_ref,
        prompt=plan.prompt,
        branch=plan.branch,
    )
    store.create_run(rec)
    workdir = tmp_path / "work"
    workdir.mkdir()

    with (
        patch("staff.lifecycle_fence.compose_fenced", side_effect=_fake_compose_fenced),
        patch("staff.cloud_dispatch.resolve_execute_launch", return_value=(["codex", "x"], "codex", None)),
        patch("staff.runner_ops.apply_staff_run_github_child_env", side_effect=lambda *a, **k: a[2]),
        patch("staff.runner_ops.sanitize_launch_env", side_effect=lambda env, **k: env),
        patch("staff.runner_ops.apply_staff_run_fleet_token", side_effect=lambda *a, **k: a[4]),
        patch("subprocess.Popen") as mock_popen,
    ):
        mock_proc = mock_popen.return_value
        mock_proc.pid = 1
        mock_proc.stdin = None
        mock_proc.stdout = iter(["STAFF_RESULT: ok\n"])
        mock_proc.wait.return_value = 0
        mock_proc.poll.return_value = 0
        runner._execute(rec, plan, workdir, lease_note="")

    assert seen["cap"] == TOTAL_CONTEXT_TOKEN_CAP
