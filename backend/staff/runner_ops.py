"""Low-level execution helpers for StaffRunner (#1586, #1587, #1588, #1593).

Extracted from staff.runner to keep file lengths within the 500-line budget.
Contains provider selection, launch arguments resolution, and subprocess output pumping.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import provider_switch
from staff import cli_version, workspace
from staff import quota as quota_mod
from staff.adapters import ProviderAdapter
from staff.anthropic_api_budget import PROVIDER_ID as ANTHROPIC_API_PROVIDER_ID
from staff.cloud_dispatch import scrub_api_key_env
from staff.github_run_env import prepare_staff_run_github_env
from staff.plan import RunPlan, RunRequest
from staff.redaction import redact_sensitive_content
from staff.roles import RoleSpec
from staff.runner_stream import (  # noqa: F401  re-exported for existing importers (#2803)
    RESULT_NUDGE_PROMPT,
    PumpResult,
    execute_retry_nudge,
    extract_transcript_question,
    pump_nudge_output,
    pump_output,
)
from staff.store import RunRecord, RunStore, _now
from staff.tokens import mint_run_token

log = logging.getLogger("dashboard.staff.runner_ops")

_GITHUB_AUTH_REMEDIATION = (
    "On the fleet hub (hub-a), configure GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID, "
    "and GITHUB_APP_PRIVATE_KEY_FILE. On spoke nodes, ensure HUB_URL and HUB_FLEET_TOKEN "
    "are set so staff runs can mint tokens via POST /api/fleet/run-token on the hub. "
    "STAFF_ALLOW_OPERATOR_TOKEN=1 is hub-only and does not apply on fleet nodes."
)

_PYTHON_VERSION_SNIPPET = "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"


@dataclass(frozen=True)
class WorktreeBootstrapPreflight:
    ok: bool
    attention: str = ""
    python_executable: str = ""
    pre_pr_entrypoint: str = ""


def resolve_repo_pre_pr_entrypoint(worktree: Path, *, python_executable: str = "python3") -> str | None:
    """Repo-local pre-PR check command for prompts and diagnostics (#2796).

    Preference order is specific -> generic. ``scripts.pre_pr`` is RM-only, so
    other repositories need their own local entry point.
    """
    entries = (
        ("scripts/pre_pr.py", f"{python_executable} -m scripts.pre_pr"),
        ("shared_scripts/pre_pr.py", f"{python_executable} shared_scripts/pre_pr.py"),
        ("scripts/verify_merge_gates.py", f"{python_executable} scripts/verify_merge_gates.py --repository-root ."),
        ("scripts/agent-check", f"{python_executable} scripts/agent-check"),
    )
    for rel, command in entries:
        if (worktree / rel).is_file():
            return command
    return None


_Run = Callable[..., subprocess.CompletedProcess[str]]
_Which = Callable[[str], str | None]


def _run_checked(
    run: _Run,
    worktree: Path,
    command: list[str],
    *,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    return run(
        command,
        cwd=str(worktree),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _python_version(worktree: Path, python_executable: str, run: _Run) -> tuple[int, int] | None:
    proc = _run_checked(run, worktree, [python_executable, "-c", _PYTHON_VERSION_SNIPPET], timeout=30)
    if proc.returncode != 0:
        return None
    raw = (proc.stdout or "").strip()
    parts = raw.split(".", 1)
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    return int(parts[0]), int(parts[1])


def _select_python311_or_newer(worktree: Path, run: _Run, which: _Which) -> str | None:
    seen: set[str] = set()
    for candidate in ("python3.11", "python3", "python"):
        executable = (which(candidate) or "").strip()
        if not executable or executable in seen:
            continue
        seen.add(executable)
        version = _python_version(worktree, executable, run)
        if version is not None and version >= (3, 11):
            return executable
    return None


def run_worktree_bootstrap_preflight(
    worktree: Path,
    *,
    run: _Run = subprocess.run,
    which: _Which = shutil.which,
) -> WorktreeBootstrapPreflight:
    """Bootstrap checks before a staff run starts coding in a worktree.

    Ensures submodules are populated, Python is >=3.11, pytest is available,
    and the repository has a local pre-PR entry point.
    """
    if (worktree / ".gitmodules").is_file():
        submodule = _run_checked(run, worktree, ["git", "submodule", "update", "--init", "--recursive"], timeout=900)
        if submodule.returncode != 0:
            detail = (submodule.stderr or submodule.stdout or "unknown git submodule error").strip()
            return WorktreeBootstrapPreflight(ok=False, attention=f"submodule init failed: {detail}")

    python_executable = _select_python311_or_newer(worktree, run, which)
    if not python_executable:
        return WorktreeBootstrapPreflight(
            ok=False,
            attention="python 3.11+ is required in the worktree environment, but no compatible interpreter was found",
        )

    pytest_check = _run_checked(run, worktree, [python_executable, "-m", "pytest", "--version"], timeout=60)
    if pytest_check.returncode != 0:
        install = _run_checked(run, worktree, [python_executable, "-m", "pip", "install", "pytest"], timeout=900)
        if install.returncode != 0:
            detail = (install.stderr or install.stdout or "pip install failed").strip()
            return WorktreeBootstrapPreflight(
                ok=False,
                attention=f"pytest is unavailable and could not be installed with {python_executable}: {detail}",
                python_executable=python_executable,
            )
        pytest_check = _run_checked(run, worktree, [python_executable, "-m", "pytest", "--version"], timeout=60)
        if pytest_check.returncode != 0:
            detail = (pytest_check.stderr or pytest_check.stdout or "pytest still unavailable").strip()
            return WorktreeBootstrapPreflight(
                ok=False,
                attention=f"pytest remains unavailable for {python_executable} after install attempt: {detail}",
                python_executable=python_executable,
            )

    pre_pr = resolve_repo_pre_pr_entrypoint(worktree, python_executable=python_executable)
    if not pre_pr:
        return WorktreeBootstrapPreflight(
            ok=False,
            attention=(
                "no repository pre-PR entry point found (checked scripts/pre_pr.py, "
                "shared_scripts/pre_pr.py, scripts/verify_merge_gates.py and scripts/agent-check)"
            ),
            python_executable=python_executable,
        )

    return WorktreeBootstrapPreflight(
        ok=True,
        python_executable=python_executable,
        pre_pr_entrypoint=pre_pr,
    )


def ensure_worktree_bootstrap_ready(
    store: RunStore,
    rec: RunRecord,
    plan: RunPlan,
    workdir: Path,
) -> WorktreeBootstrapPreflight | None:
    """Run bootstrap preflight and fail fast with ``environment_error`` when it fails."""
    if not plan.repo:
        return WorktreeBootstrapPreflight(ok=True)
    result = run_worktree_bootstrap_preflight(workdir)
    if result.ok:
        if result.pre_pr_entrypoint:
            store.append_event(
                rec.id,
                "preflight",
                f"worktree bootstrap ok: python={result.python_executable}; pre_pr={result.pre_pr_entrypoint}",
            )
        return result
    store.update_run(
        rec.id,
        status="failed",
        failure_class="environment_error",
        retryable=False,
        remediation="Worktree bootstrap preflight failed; fix environment prerequisites before re-dispatching.",
        error=result.attention,
        ended_at=_now(),
    )
    store.append_event(rec.id, "error", result.attention)
    return None


def prepare_cloud_workdir(store: RunStore, rec: RunRecord) -> Path:
    """Minimal workdir for cloud-session launches (no clone/worktree)."""
    workdir = workspace.staff_worktrees_root() / rec.id / "cloud"
    workdir.mkdir(parents=True, exist_ok=True)
    store.update_run(rec.id, workdir=str(workdir))
    store.append_event(rec.id, "cloud", f"cloud session workdir {workdir}")
    return workdir


def prepare_run_workdir(store: RunStore, rec: RunRecord, plan: RunPlan) -> Path:
    """Resolve clone/worktree for ``rec``; persists ``workdir`` on the run row."""
    if not plan.repo:
        workdir = workspace.staff_worktrees_root() / rec.id
        workdir.mkdir(parents=True, exist_ok=True)
        store.update_run(rec.id, workdir=str(workdir))
        return workdir
    checkout = workspace.find_repo_checkout(plan.repo)
    if checkout is None:
        store.append_event(rec.id, "clone", f"no local checkout of {plan.repo}; cloning")
        checkout = workspace.clone_repo(plan.repo)
    worktree = workspace.staff_worktrees_root() / f"{plan.repo}-{rec.id}"
    workspace.add_run_worktree(checkout, worktree, plan.branch, plan.head_ref)
    store.update_run(rec.id, workdir=str(worktree))
    store.append_event(rec.id, "worktree", f"{worktree} on {plan.branch}")
    return worktree


def apply_staff_run_github_child_env(
    store: RunStore,
    rec: RunRecord,
    base_env: Mapping[str, str],
    role: RoleSpec,
    repo: str,
    *,
    run_id: str,
    real_gh_path: str,
) -> dict[str, str] | None:
    """Mint GitHub child env or mark the run ``failed`` with ``auth_error`` (#2038)."""
    try:
        env, _cred = prepare_staff_run_github_env(
            base_env,
            role,
            repo,
            run_id=run_id,
            real_gh_path=real_gh_path,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed to mint GitHub credential for staff run %s", run_id)
        err = redact_sensitive_content(f"GitHub auth for run failed: {exc}")
        store.update_run(
            rec.id,
            status="failed",
            failure_class="auth_error",
            retryable=False,
            remediation=_GITHUB_AUTH_REMEDIATION,
            error=err,
            ended_at=_now(),
        )
        store.append_event(rec.id, "error", err)
        return None
    return env


def sanitize_launch_env(env: Mapping[str, str], *, execution_mode: str, provider: str) -> dict[str, str]:
    """Strip provider API keys from a launch env unless this is the anthropic-api launch itself (#3534).

    Pre: ``env`` is the env about to be handed to a staff CLI subprocess.
    Post: every launch other than the ``anthropic-api`` provider's own local run
    (cloud dispatch already scrubs unconditionally) never carries a provider API
    key, even once Dieter configures one for anthropic-api's exclusive use.
    """
    if execution_mode == "cloud" or provider != ANTHROPIC_API_PROVIDER_ID:
        return scrub_api_key_env(env)
    return dict(env)


def apply_staff_run_fleet_token(
    store: RunStore,
    rec: RunRecord,
    plan: RunPlan,
    role: RoleSpec,
    env: dict[str, str],
    adapter: ProviderAdapter,
    *,
    wall_clock_timeout: float,
) -> dict[str, str] | None:
    """Attach fleet API token and adapter runtime env, or fail the run on mint errors."""
    try:
        token = mint_run_token(
            plan.role, rec.id, role.fleet_actions, ttl_seconds=wall_clock_timeout, library_read=role.library_read
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed to mint staff run token for %s", rec.id)
        err = f"token minting failed: {exc}"
        store.update_run(
            rec.id,
            status="failed",
            failure_class="workspace_error",
            retryable=False,
            remediation="Token minting failed; verify node identity configuration.",
            error=err,
            ended_at=_now(),
        )
        store.append_event(rec.id, "error", err)
        return None
    return {
        **env,
        **adapter.runtime_env(),
        "STAFF_RUN_ID": rec.id,
        "STAFF_ROLE": plan.role,
        "FLEET_API_TOKEN": token,
    }


def can_run_unattended(adapters: Mapping[str, ProviderAdapter], pid: str) -> bool:
    """Known, not chat-only (#1586), not switched off on this node (#1597), CLI not below its floor (#1680)."""
    return (
        pid in adapters
        and getattr(adapters[pid], "unattended", True)
        and not provider_switch.is_disabled(pid)
        and not cli_version.is_outdated(getattr(adapters[pid], "executable", ""))
    )


def fail_if_cli_outdated(store: RunStore, rec: RunRecord, adapter: ProviderAdapter) -> bool:
    """Fail ``rec`` up front when the adapter's CLI is below its minimum version (#1680).

    Post: when True, the run is ``failed`` with the non-retryable ``cli_outdated`` class
    and nothing was launched; when False, the run is untouched.
    """
    gate = cli_version.version_gate(getattr(adapter, "executable", ""))
    if gate is None:
        return False
    store.update_run(
        rec.id,
        status="failed",
        failure_class=gate.failure_class,
        retryable=gate.retryable,
        remediation=gate.remediation,
        error=gate.error,
        ended_at=_now(),
    )
    store.append_event(rec.id, "error", gate.remediation)
    return True


def select_first_available_provider(
    adapters: Mapping[str, ProviderAdapter],
    providers: tuple[str, ...],
    ceiling: float | None = None,
) -> str:
    """First installed, unattended provider whose plan is under ``ceiling`` percent (#1586, #1588).

    Post: when every installed provider is over quota, the first installed one
    (the gates in the scheduler and dispatch decide whether it may run); when
    none is installed, the first that can run unattended.
    """
    runnable = [pid for pid in providers if can_run_unattended(adapters, pid)]
    installed = [pid for pid in runnable if adapters[pid].installed()]
    limit = quota_mod.ceiling_percent(None) if ceiling is None else ceiling
    for pid in installed:
        if quota_mod.headroom(pid, limit)[0]:
            return pid
    if installed:
        return installed[0]
    if runnable:
        return runnable[0]
    return providers[0] if providers else "claude"


class ReadOnlyKwargs(TypedDict, total=False):
    read_only: bool


class LaunchPaths(TypedDict, total=False):
    gitdir: str
    policy: str


def read_only_kwargs(role: RoleSpec) -> ReadOnlyKwargs:
    """``read_only`` keyword for ``adapter.build_command`` (#1659), passed only when set.

    Post: empty for a normal role, so an adapter with the older signature still works.
    """
    return {"read_only": True} if role.code_read_only else {}


def resolve_launch_paths(adapter: ProviderAdapter, workdir: Path) -> LaunchPaths:
    """``gitdir``/``policy`` keyword arguments for ``adapter.build_command`` (#1586).

    Post: only the slots the adapter's argv actually uses are passed, so an
    adapter with the older ``build_command(prompt, workdir, model)`` shape still works.
    """
    argv = getattr(adapter, "argv", ())
    paths: LaunchPaths = {}
    if any("{gitdir}" in part for part in argv):
        paths["gitdir"] = str(workspace.git_common_dir(workdir))
    policy_text = getattr(adapter, "policy_text", None)
    if policy_text is not None and any("{policy}" in part for part in argv):
        paths["policy"] = str(workspace.write_policy_file(adapter.provider_id, policy_text()))
    return paths


def new_run_record(plan: RunPlan, req: RunRequest, machine: str, max_attempts: int, run_id: str | None) -> RunRecord:
    """The ``queued`` row for an admitted run; ``run_id`` defaults to a fresh id."""
    return RunRecord(
        id=run_id or f"run-{uuid.uuid4().hex[:12]}",
        role=plan.role,
        provider=plan.provider,
        model=plan.model,
        machine=machine,
        repo=plan.repo,
        target_kind=plan.target_kind,
        target_ref=plan.target_ref,
        prompt=plan.prompt,
        requested_by=req.requested_by,
        dispatch_origin=req.dispatch_origin,
        on_behalf_of=req.on_behalf_of,
        branch=plan.branch,
        strategy_mode=plan.strategy_mode,
        model_tier=getattr(plan, "model_tier", "") or "",
        max_attempts=max_attempts,
        thread_id=req.thread_id,
        work_item_id=req.work_item_id,
        origin_node=getattr(req, "origin_node", "") or "",
        skill_pin=getattr(plan, "skill_pin", "") or "",
        standards_version=getattr(plan, "standards_version", "") or "",
        standards_tokens=getattr(plan, "standards_tokens", 0) or 0,
        context_stamp=getattr(plan, "context_stamp", "{}") or "{}",
        library_work_ids=json.dumps(list(getattr(plan, "library_work_ids", ()) or ())),
        injected_lesson_ids=json.dumps(list(getattr(plan, "injected_lesson_ids", ()) or ())),
        skipped_lesson_ids=json.dumps(dict(getattr(plan, "skipped_lesson_ids", {}) or {})),
        lessons_tokens=int(getattr(plan, "lessons_tokens", 0) or 0),
        execution_mode=getattr(req, "execution_mode", "local") or "local",
    )
