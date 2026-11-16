"""Slice 1 (A-03): skill block in the planned prompt must reach the provider launch argv (#4266)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from staff.adapters import ADAPTERS, unattended_providers
from staff import workspace
from staff.dispatch_bundle import resolve_dispatch_bundle
from staff.plan import RunPlan, RunRequest
from staff.roles import RoleSpec
from staff.runner import RunRecord, StaffRunner
from staff.runner_planning import build_run_plan
from staff.skills.registry import clear_skills_cache
from staff.store import RunStore

pytestmark = pytest.mark.unit

_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "skills_dispatch"


class _StubAdapter:
    executable = "fake-cli"
    prompt_via_stdin = False
    lease_agent = "fake-cli"
    unattended = True

    def installed(self) -> bool:
        return True

    def build_command(self, prompt: str, workdir: str, model: str | None = None, **kwargs: Any) -> list[str]:
        return ["fake-cli", prompt]

    def runtime_env(self) -> dict[str, str]:
        return {}

    def parse_line(self, line: str) -> dict[str, Any]:
        return {"kind": "text", "text": line}


def _roles() -> dict[str, RoleSpec]:
    return {
        "night-watch": RoleSpec(
            name="night-watch",
            title="Night Watch",
            providers=("fake-cli",),
            permissions={"lease": False, "push_branch": True, "open_pr": True},
        )
    }


def _launched_skill_block(
    tmp_path: Path,
    plan: Any,
    *,
    provider: str,
    executable: str,
) -> str:
    adapter = _StubAdapter()
    adapter.executable = executable
    adapter.lease_agent = provider

    store = RunStore(tmp_path / "runs.sqlite3")
    runner = StaffRunner(store=store)
    runner._adapters = {provider: adapter}
    runner._roles_loader = _roles
    rec = RunRecord(
        id=f"run-skill-{provider}",
        role=plan.role,
        provider=provider,
        model=None,
        machine="local",
        repo=plan.repo,
        target_kind=plan.target_kind,
        target_ref=plan.target_ref,
        prompt=plan.prompt,
        branch=plan.branch,
    )
    store.create_run(rec)
    captured: dict[str, str] = {}

    def _capture(_plan: Any, _adapter: Any, prompt: str, *_a: Any, **_k: Any) -> tuple[list[str], str, None]:
        captured["prompt"] = prompt
        return ([executable, prompt], f"{executable} ({provider})", None)

    workdir = tmp_path / "work"
    workdir.mkdir()
    with (
        patch("staff.cloud_dispatch.resolve_execute_launch", side_effect=_capture),
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
    return captured["prompt"]


@pytest.fixture
def skills_env(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_skills_cache()
    monkeypatch.setenv("STAFF_SKILLS_DIR", str(_FIXTURE_ROOT))
    yield
    clear_skills_cache()


def test_planned_skill_block_is_byte_identical_at_launch(tmp_path: Path, skills_env: None) -> None:
    plan = build_run_plan(
        RunRequest(role="night-watch", provider="fake-cli", repo="AffineDrift", issue=1, prompt="sweep"),
        roles=_roles(),
        adapters={"fake-cli": _StubAdapter()},
        machine="local",
        focus_loader=lambda: [],
    )
    assert plan.dispatch_bundle is not None
    skill_block = plan.dispatch_bundle.skill_block
    assert skill_block
    assert skill_block in plan.prompt

    launched = _launched_skill_block(tmp_path, plan, provider="fake-cli", executable="fake-cli")
    start = launched.index(skill_block)
    end = start + len(skill_block)
    assert launched[start:end] == skill_block


@pytest.mark.parametrize(
    "provider",
    sorted(pid for pid, ok in unattended_providers().items() if ok),
)
def test_skill_block_survives_launch_per_adapter(
    tmp_path: Path,
    skills_env: None,
    provider: str,
) -> None:
    role = RoleSpec(
        name="night-watch",
        title="Night Watch",
        providers=(provider,),
        permissions={"lease": False, "push_branch": True, "open_pr": True},
    )
    bundle = resolve_dispatch_bundle(role="night-watch", repo="AffineDrift")
    skill_block = bundle.skill_block
    assert skill_block
    prompt = workspace.compose_prompt(
        role,
        repo="AffineDrift",
        target_ref="#1",
        operator_prompt="sweep",
        branch="staff/night-watch-1",
        dispatch_bundle=bundle,
    )
    adapter = ADAPTERS[provider]
    plan = RunPlan(
        role="night-watch",
        provider=provider,
        model=adapter.default_model,
        repo="AffineDrift",
        target_kind="issue",
        target_ref="#1",
        operator_prompt="sweep",
        prompt=prompt,
        argv=adapter.build_command(prompt, str(tmp_path), adapter.default_model),
        branch="staff/night-watch-1",
        lease_ritual=False,
        dispatch_bundle=bundle,
    )
    launched = _launched_skill_block(
        tmp_path,
        plan,
        provider=provider,
        executable=adapter.executable,
    )
    start = launched.index(skill_block)
    assert launched[start : start + len(skill_block)] == skill_block
