"""Unit tests for staff runner_ops helper functions (#1586, #1587, #1588, #1593)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from staff.plan import RunPlan
from staff.runner_ops import (
    StaffRunPreparationError,
    _ensure_disk_floor_admitted,
    prepare_run_workdir,
    pump_output,
    resolve_launch_paths,
    sanitize_launch_env,
    select_first_available_provider,
)
from staff.store import RunRecord, RunStore


class DummyAdapter:
    def __init__(
        self,
        provider_id: str = "claude",
        installed: bool = True,
        unattended: bool = True,
        argv: tuple[str, ...] = ("bin",),
        policy_text: Any = None,
    ) -> None:
        self.provider_id = provider_id
        self._installed = installed
        self.unattended = unattended
        self.argv = argv
        if policy_text is not None:
            self.policy_text = policy_text

    def installed(self) -> bool:
        return self._installed

    def parse_line(self, line: str) -> dict[str, Any]:
        return {"kind": "text", "text": line.strip()}


@pytest.mark.unit
def test_select_first_available_provider_empty() -> None:
    assert select_first_available_provider({}, ()) == "claude"


@pytest.mark.unit
def test_select_first_available_provider_selection() -> None:
    adapters = {
        "claude": DummyAdapter("claude", installed=True),
        "codex": DummyAdapter("codex", installed=True),
    }
    assert select_first_available_provider(adapters, ("claude", "codex")) == "claude"
    assert select_first_available_provider(adapters, ("codex", "claude")) == "codex"


@pytest.mark.unit
def test_resolve_launch_paths_no_placeholders(tmp_path: Path) -> None:
    adapter = DummyAdapter("claude", argv=("run", "arg"))
    paths = resolve_launch_paths(adapter, tmp_path)  # type: ignore[arg-type]
    assert paths == {}


@pytest.mark.unit
def test_resolve_launch_paths_gitdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = DummyAdapter("codex", argv=("run", "--dir", "{gitdir}"))
    monkeypatch.setattr("staff.workspace.git_common_dir", lambda p: p / ".git")
    paths = resolve_launch_paths(adapter, tmp_path)  # type: ignore[arg-type]
    assert paths == {"gitdir": str(tmp_path / ".git")}


@pytest.mark.unit
def test_pump_output_streams_and_extracts_result(tmp_path: Path) -> None:
    rec = RunRecord(
        id="run-test123",
        role="worker",
        provider="claude",
        model="claude-3-7-sonnet",
        machine="testbox",
        repo="D-sorganization/Runner_Dashboard",
        target_kind="issue",
        target_ref="1593",
        prompt="fix it",
    )
    adapter = DummyAdapter("claude")
    transcript = tmp_path / "transcript.log"
    store = MagicMock(spec=RunStore)

    proc = MagicMock()
    proc.stdout = ["hello agent\n", "STAFF_RESULT: done successfully\n"]

    usage, result = pump_output(rec, adapter, proc, transcript, store)  # type: ignore[arg-type]

    assert result == "STAFF_RESULT: done successfully"
    assert transcript.exists()
    content = transcript.read_text(encoding="utf-8")
    assert "hello agent" in content
    assert "STAFF_RESULT: done successfully" in content
    assert store.append_event.call_count == 2


@pytest.mark.parametrize("provider", ["claude", "codex", "antigravity", "cursor-agent"])
def test_sanitize_launch_env_strips_provider_keys_for_every_other_provider(provider: str) -> None:
    """#3534: only the anthropic-api launch itself may ever see a provider API key."""
    env = {
        "PATH": "/usr/bin",
        "ANTHROPIC_API_KEY": "sk-secret",  # pragma: allowlist secret
        "OPENAI_API_KEY": "sk-openai",  # pragma: allowlist secret
    }
    cleaned = sanitize_launch_env(env, execution_mode="local", provider=provider)
    assert "ANTHROPIC_API_KEY" not in cleaned
    assert "OPENAI_API_KEY" not in cleaned
    assert cleaned["PATH"] == "/usr/bin"


def test_sanitize_launch_env_keeps_key_for_anthropic_api_local_launch() -> None:
    env = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-secret"}  # pragma: allowlist secret
    cleaned = sanitize_launch_env(env, execution_mode="local", provider="anthropic-api")
    assert cleaned["ANTHROPIC_API_KEY"] == "sk-secret"  # pragma: allowlist secret


def test_sanitize_launch_env_strips_key_for_anthropic_api_cloud_launch() -> None:
    """Cloud dispatch never gets a key, even for the anthropic-api provider itself (#2627 invariant)."""
    env = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-secret"}  # pragma: allowlist secret
    cleaned = sanitize_launch_env(env, execution_mode="cloud", provider="anthropic-api")
    assert "ANTHROPIC_API_KEY" not in cleaned


class _WorkdirHarness:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, free_gb: float = 50.0) -> None:
        self.wt_root = tmp_path / "staff-worktrees"
        self.latch = tmp_path / "disk-floor.json"
        self.free_bytes = int(free_gb * 1e9)
        self.updates: dict[str, Any] = {}
        self.events: list[tuple[str, str]] = []
        schedule = tmp_path / "runner-schedule.json"
        schedule.write_text(
            json.dumps(
                {
                    "resource_floor": {
                        "disk_path": str(tmp_path / "vol"),
                        "min_disk_free_gb": 40,
                        "readmit_disk_free_gb": 45,
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("staff.disk_floor_shared.DEFAULT_RUNNER_SCHEDULE_CONFIG", schedule)
        monkeypatch.setattr("staff.disk_floor_shared.DISK_FLOOR_LATCH_PATH", self.latch)
        monkeypatch.setattr(
            "staff.disk_floor_shared.shutil.disk_usage", lambda _p: SimpleNamespace(free=self.free_bytes)
        )
        monkeypatch.setattr("staff.workspace.staff_worktrees_root", lambda: self.wt_root)
        store = MagicMock(spec=RunStore)
        store.update_run.side_effect = lambda _rid, **kw: self.updates.update(kw)
        store.append_event.side_effect = lambda _rid, kind, msg: self.events.append((kind, msg))
        self.store = store
        self.rec = RunRecord(
            id="run-4212",
            role="worker",
            provider="codex",
            model="gpt-6.1-sol",
            machine="testbox",
            repo="Tools",
            target_kind="issue",
            target_ref="#4212",
            prompt="fix",
        )

    def make_plan(self, repo: str = "Tools", head_ref: str = "") -> RunPlan:
        return RunPlan(
            role="worker",
            provider="codex",
            model="gpt-6.1-sol",
            repo=repo,
            target_kind="issue",
            target_ref="#4212",
            operator_prompt="fix",
            prompt="fix",
            argv=["codex"],
            branch="staff/4212",
            lease_ritual=False,
            head_ref=head_ref,
        )


@pytest.mark.unit
def test_prepare_run_workdir_missing_checkout_blocks_clone_when_disk_floor_breached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4212: missing checkout + breached floor must not clone or add a worktree."""
    harness = _WorkdirHarness(tmp_path, monkeypatch, free_gb=35.0)
    clone_mock = MagicMock(side_effect=OSError(28, "No space left on device"))
    add_wt_mock = MagicMock()
    sweep_mock = MagicMock(return_value={"removed": 0, "preserved_unpushed": 1, "skipped_active": 0})
    monkeypatch.setattr("staff.workspace.find_repo_checkout", lambda _repo: None)
    monkeypatch.setattr("staff.workspace.clone_repo", clone_mock)
    monkeypatch.setattr("staff.workspace.add_run_worktree", add_wt_mock)
    monkeypatch.setattr("staff.workspace.sweep_stale_worktrees", sweep_mock)

    with pytest.raises(StaffRunPreparationError, match="runner schedule disk floor breached"):
        prepare_run_workdir(harness.store, harness.rec, harness.make_plan())

    clone_mock.assert_not_called()
    add_wt_mock.assert_not_called()
    sweep_mock.assert_called_once_with(harness.store)
    assert all(kind != "clone" for kind, _ in harness.events)
    assert any(kind == "worktree" and "preserved_unpushed" in msg for kind, msg in harness.events)
    assert harness.updates["status"] == "failed"
    assert harness.updates["failure_class"] == "workspace_error"
    assert harness.updates["retryable"] is True
    assert not harness.latch.exists()


@pytest.mark.unit
def test_prepare_run_workdir_missing_checkout_honors_scheduler_readmit_hysteresis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4212: scheduler readmit latch blocks initial clone without unlinking the latch."""
    harness = _WorkdirHarness(tmp_path, monkeypatch, free_gb=44.0)
    harness.latch.write_text('{"from":"scheduler"}', encoding="utf-8")
    clone_mock = MagicMock()
    add_wt_mock = MagicMock()
    sweep_mock = MagicMock(return_value={"removed": 0, "preserved_unpushed": 0, "skipped_active": 0})
    monkeypatch.setattr("staff.workspace.find_repo_checkout", lambda _repo: None)
    monkeypatch.setattr("staff.workspace.clone_repo", clone_mock)
    monkeypatch.setattr("staff.workspace.add_run_worktree", add_wt_mock)
    monkeypatch.setattr("staff.workspace.sweep_stale_worktrees", sweep_mock)

    with pytest.raises(StaffRunPreparationError, match="readmit"):
        prepare_run_workdir(harness.store, harness.rec, harness.make_plan())

    clone_mock.assert_not_called()
    add_wt_mock.assert_not_called()
    assert harness.updates["failure_class"] == "workspace_error"
    assert harness.updates["retryable"] is True
    assert harness.latch.read_text(encoding="utf-8") == '{"from":"scheduler"}'


@pytest.mark.unit
def test_prepare_run_workdir_existing_checkout_blocks_worktree_when_floor_breached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4212 control: existing checkout still blocks worktree creation on floor breach."""
    harness = _WorkdirHarness(tmp_path, monkeypatch, free_gb=35.0)
    clone_mock = MagicMock()
    add_wt_mock = MagicMock()
    sweep_mock = MagicMock(return_value={"removed": 0, "preserved_unpushed": 0, "skipped_active": 1})
    monkeypatch.setattr("staff.workspace.find_repo_checkout", lambda _repo: tmp_path / "existing")
    monkeypatch.setattr("staff.workspace.clone_repo", clone_mock)
    monkeypatch.setattr("staff.workspace.add_run_worktree", add_wt_mock)
    monkeypatch.setattr("staff.workspace.sweep_stale_worktrees", sweep_mock)

    with pytest.raises(StaffRunPreparationError, match="runner schedule disk floor breached"):
        prepare_run_workdir(harness.store, harness.rec, harness.make_plan())

    clone_mock.assert_not_called()
    add_wt_mock.assert_not_called()
    sweep_mock.assert_called_once_with(harness.store)
    assert harness.updates["failure_class"] == "workspace_error"
    assert harness.updates["retryable"] is True


@pytest.mark.unit
def test_prepare_run_workdir_missing_checkout_admits_after_permitted_sweep_clears_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4212 positive control: pre-clone sweep clears floor (preserving unpushed), then clone+worktree succeed."""
    harness = _WorkdirHarness(tmp_path, monkeypatch, free_gb=35.0)
    cloned_checkout = tmp_path / "_staff_clones" / "Tools"

    def _sweep(_store: Any) -> dict[str, int]:
        harness.free_bytes = int(46e9)
        return {"removed": 2, "preserved_unpushed": 1, "skipped_active": 0}

    def _clone(repo: str) -> Path:
        assert repo == "Tools"
        assert harness.free_bytes >= int(40e9), "clone ran before pre-clone sweep cleared disk floor"
        harness.free_bytes = int(43e9)
        return cloned_checkout

    add_wt_mock = MagicMock()
    monkeypatch.setattr("staff.workspace.find_repo_checkout", lambda _repo: None)
    monkeypatch.setattr("staff.workspace.sweep_stale_worktrees", _sweep)
    monkeypatch.setattr("staff.workspace.clone_repo", _clone)
    monkeypatch.setattr("staff.workspace.add_run_worktree", add_wt_mock)

    result = prepare_run_workdir(harness.store, harness.rec, harness.make_plan(head_ref="pr-head"))

    expected_wt = harness.wt_root / "Tools-run-4212"
    assert result == expected_wt
    add_wt_mock.assert_called_once_with(cloned_checkout, expected_wt, "staff/4212", "pr-head")
    assert harness.updates["workdir"] == str(expected_wt)
    assert [kind for kind, _ in harness.events] == ["worktree", "clone", "worktree"]


@pytest.mark.unit
@pytest.mark.parametrize("initial_free_gb", [41.0, 35.0])
def test_prepare_run_workdir_rechecks_disk_floor_after_clone_consumes_headroom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, initial_free_gb: float
) -> None:
    """#4212: final pre-worktree check catches disk breach introduced by clone_repo."""
    harness = _WorkdirHarness(tmp_path, monkeypatch, free_gb=initial_free_gb)
    cloned_checkout = tmp_path / "_staff_clones" / "Tools"
    sweep_calls = 0

    def _sweep(_store: Any) -> dict[str, int]:
        nonlocal sweep_calls
        sweep_calls += 1
        if harness.free_bytes == int(35e9):
            harness.free_bytes = int(41e9)
            return {"removed": 1, "preserved_unpushed": 1, "skipped_active": 0}
        return {"removed": 0, "preserved_unpushed": 1, "skipped_active": 0}

    def _clone(_repo: str) -> Path:
        harness.free_bytes = int(38e9)
        return cloned_checkout

    clone_mock = MagicMock(side_effect=_clone)
    add_wt_mock = MagicMock()
    monkeypatch.setattr("staff.workspace.find_repo_checkout", lambda _repo: None)
    monkeypatch.setattr("staff.workspace.sweep_stale_worktrees", _sweep)
    monkeypatch.setattr("staff.workspace.clone_repo", clone_mock)
    monkeypatch.setattr("staff.workspace.add_run_worktree", add_wt_mock)

    with pytest.raises(StaffRunPreparationError, match="runner schedule disk floor breached"):
        prepare_run_workdir(harness.store, harness.rec, harness.make_plan())

    clone_mock.assert_called_once_with("Tools")
    add_wt_mock.assert_not_called()
    assert sweep_calls == 1
    assert harness.updates["status"] == "failed"
    assert harness.updates["failure_class"] == "workspace_error"
    assert harness.updates["retryable"] is True


@pytest.mark.unit
def test_prepare_run_workdir_keeps_healthy_and_repoless_runs_functional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4212: healthy missing-checkout, repo-less runs, and DbC stage guard remain intact."""
    harness = _WorkdirHarness(tmp_path, monkeypatch, free_gb=50.0)
    with pytest.raises(ValueError, match="stage must be non-empty"):
        _ensure_disk_floor_admitted(harness.store, harness.rec, stage=" ")

    cloned_checkout = tmp_path / "_staff_clones" / "Tools"
    clone_mock = MagicMock(return_value=cloned_checkout)
    add_wt_mock = MagicMock()
    sweep_mock = MagicMock()
    monkeypatch.setattr("staff.workspace.find_repo_checkout", lambda _repo: None)
    monkeypatch.setattr("staff.workspace.clone_repo", clone_mock)
    monkeypatch.setattr("staff.workspace.add_run_worktree", add_wt_mock)
    monkeypatch.setattr("staff.workspace.sweep_stale_worktrees", sweep_mock)

    wt = prepare_run_workdir(harness.store, harness.rec, harness.make_plan())
    assert wt == harness.wt_root / "Tools-run-4212"
    clone_mock.assert_called_once_with("Tools")
    add_wt_mock.assert_called_once()
    sweep_mock.assert_not_called()

    repoless = prepare_run_workdir(harness.store, harness.rec, harness.make_plan(repo=""))
    assert repoless == harness.wt_root / "run-4212"
    assert repoless.is_dir()


@pytest.mark.unit
def test_apply_staff_run_fleet_token_passes_library_read_from_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RM#2393: runner_ops mints ``library.read`` only when the role spec opts in; YAML ``scopes`` are not forwarded."""
    import identity
    from identity import identity_manager, principal_has_scope
    from staff.role_spec import RoleSpec
    from staff.runner_ops import apply_staff_run_fleet_token
    from staff.tokens import revoke_run_token
    import staff.tokens as tokens_mod

    manager = identity.IdentityManager(tmp_path / "identity")
    monkeypatch.setattr(identity, "identity_manager", manager)
    monkeypatch.setattr("staff.tokens.identity_manager", manager)
    monkeypatch.setattr(tokens_mod, "_active_run_tokens", {})
    presets = dict(identity.SCOPE_PRESETS)
    monkeypatch.setattr(identity, "SCOPE_PRESETS", presets)
    monkeypatch.setattr(tokens_mod, "SCOPE_PRESETS", presets)

    store = RunStore(tmp_path / "runs.sqlite3")
    rec = RunRecord(
        id="run-2393-lib",
        role="research-scout",
        provider="claude",
        model="claude-test",
        machine="testbox",
        repo="Tools",
        target_kind="issue",
        target_ref="2393",
        prompt="read library",
        status="running",
    )
    store.create_run(rec)
    plan = RunPlan(
        role="research-scout",
        provider="claude",
        model="claude-test",
        repo="Tools",
        target_kind="issue",
        target_ref="2393",
        operator_prompt="read",
        prompt="read library",
        argv=["claude"],
        branch="staff/2393",
        lease_ritual=False,
    )
    role = RoleSpec(
        name="research-scout",
        title="Scout",
        permissions={"library_read": True, "fleet_actions": ["runner.start"]},
    )
    adapter = DummyAdapter("claude")
    adapter.runtime_env = lambda: {}  # type: ignore[method-assign]

    env = apply_staff_run_fleet_token(store, rec, plan, role, {}, adapter, wall_clock_timeout=300.0)
    assert env is not None
    token = env["FLEET_API_TOKEN"]
    assert token.startswith("run_")
    prin = manager.verify_token(token)
    assert prin is not None
    assert principal_has_scope(prin, "library.read")
    assert principal_has_scope(prin, "runners.control")
    assert not principal_has_scope(prin, "proposals.write")
    assert not principal_has_scope(prin, "admin")
    revoke_run_token(rec.id)
