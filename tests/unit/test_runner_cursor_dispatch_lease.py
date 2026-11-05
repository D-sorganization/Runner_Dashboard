"""Runner integration for the Cursor dispatch pre-launch lease re-check (#3931 S2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from staff.github_run_env import StaffGithubToken
from staff.plan import RunPlan
from staff.runner import RunRecord, StaffRunner
from staff.store import RunStore

pytestmark = pytest.mark.unit

_FAKE_GHS = "ghs_" + "A" * 40 + "_jwtbody"
_FAKE_GITHUB_ENV = {"PATH": "/bin", "GH_TOKEN": _FAKE_GHS, "GITHUB_TOKEN": _FAKE_GHS}
_FAKE_GITHUB_CRED = StaffGithubToken(token=_FAKE_GHS, expires_at=0.0, source="github_app")


class _DummyCloudAdapter:
    executable = "cursor-agent"
    prompt_via_stdin = False
    lease_agent = "cursor-agent"

    def installed(self) -> bool:
        return True

    def build_command(self, prompt: str, workdir: str, model: str | None = None, **kwargs: Any) -> list[str]:
        return ["cursor-agent"]

    def runtime_env(self) -> dict[str, str]:
        return {}

    def parse_line(self, line: str) -> dict[str, Any]:
        return {"kind": "text", "text": line}


def _cursor_plan() -> RunPlan:
    return RunPlan(
        role="maintenance",
        provider="cursor-agent",
        model="default",
        repo="D-sorganization/Runner_Dashboard",
        target_kind="issue",
        target_ref="#101",
        operator_prompt="",
        prompt="test",
        argv=[],
        branch="",
        lease_ritual=False,
        execution_mode="cloud",
    )


def _runner(tmp_path: Path) -> tuple[StaffRunner, RunStore, RunRecord]:
    store = RunStore(tmp_path / "runs.db")
    runner = StaffRunner(store=store)
    runner._adapters = {"cursor-agent": _DummyCloudAdapter()}
    from staff.roles import RoleSpec

    role = RoleSpec(
        name="maintenance",
        title="Maintenance",
        providers=("cursor-agent",),
        permissions={
            "lease": False,
            "push_branch": True,
            "open_pr": True,
            "merge": False,
            "host_shell": False,
            "notify_user": False,
        },
    )
    runner._roles_loader = lambda: {"maintenance": role}
    rec = RunRecord(
        id="run-cursor-lease-test",
        role="maintenance",
        provider="cursor-agent",
        model="default",
        machine="local",
        repo="D-sorganization/Runner_Dashboard",
        target_kind="issue",
        target_ref="#101",
        prompt="test",
    )
    store.create_run(rec)
    return runner, store, rec


def test_pre_launch_block_prevents_popen_when_another_run_holds_the_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DASHBOARD_PATH_LEASE_DB_PATH", str(tmp_path / "path_leases.sqlite3"))
    runner, store, rec = _runner(tmp_path)
    plan = _cursor_plan()
    workdir = tmp_path / "work"
    workdir.mkdir()

    from coordination.cursor_dispatch_lease import pre_launch_lease_recheck

    held = pre_launch_lease_recheck("D-sorganization/Runner_Dashboard", 101, None, "another-run", 3600.0)
    assert held is not None

    with (
        patch(
            "staff.cloud_dispatch.resolve_execute_launch",
            return_value=(["cursor-agent"], "cursor-agent (cloud)", None),
        ),
        patch(
            "staff.runner_ops.prepare_staff_run_github_env",
            return_value=(_FAKE_GITHUB_ENV, _FAKE_GITHUB_CRED),
        ),
        patch("subprocess.Popen") as mock_popen,
    ):
        runner._execute(rec, plan, workdir, lease_note="")
    assert not mock_popen.called

    updated = store.get_run(rec.id)
    assert updated is not None
    assert updated.status == "blocked"
    assert updated.failure_class == "cursor_path_lease_blocked"


def test_pre_launch_passes_through_and_releases_lease_after_publish_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DASHBOARD_PATH_LEASE_DB_PATH", str(tmp_path / "path_leases.sqlite3"))
    runner, store, rec = _runner(tmp_path)
    plan = _cursor_plan()
    workdir = tmp_path / "work"
    workdir.mkdir()

    from path_lease_store import PathLeaseStore, default_path_lease_db_path

    def capture_call(*args: Any, **kwargs: Any) -> Any:
        mock_proc = MagicMock()
        mock_proc.pid = 4242
        mock_proc.stdin = None
        mock_proc.stdout = iter(["STAFF_RESULT: ok\n"])
        mock_proc.wait.return_value = 0
        mock_proc.poll.return_value = 0
        return mock_proc

    with (
        patch(
            "staff.cloud_dispatch.resolve_execute_launch",
            return_value=(["cursor-agent"], "cursor-agent (cloud)", None),
        ),
        patch(
            "staff.runner_ops.prepare_staff_run_github_env",
            return_value=(_FAKE_GITHUB_ENV, _FAKE_GITHUB_CRED),
        ),
        patch("subprocess.Popen", side_effect=capture_call),
        patch("staff.cloud_dispatch.record_cloud_session_from_transcript"),
    ):
        runner._execute(rec, plan, workdir, lease_note="")

    store2 = PathLeaseStore(default_path_lease_db_path())
    assert store2.check("D-sorganization/Runner_Dashboard", "issue:101") is None
