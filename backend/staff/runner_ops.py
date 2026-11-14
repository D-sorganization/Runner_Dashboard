"""Low-level execution helpers for StaffRunner (#1586, #1587, #1588, #1593).

Extracted from staff.runner to keep file lengths within the 500-line budget.
Contains provider selection, launch arguments resolution, and subprocess output pumping.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import TypedDict

import provider_switch
from staff.adapters import ProviderAdapter
from staff.anthropic_api_budget import PROVIDER_ID as ANTHROPIC_API_PROVIDER_ID
from staff.cloud_dispatch import scrub_api_key_env
from staff.disk_floor_shared import staff_disk_floor_breach_reason
from staff.github_run_env import prepare_staff_run_github_env
from staff.plan import RunPlan, RunRequest
from staff.redaction import redact_sensitive_content
from staff.run_status import RunStatus, transition_to
from staff.runner_ops_worktree_bootstrap import (  # noqa: F401  re-exported for existing importers (#3913)
    _PYTHON_VERSION_SNIPPET,
    _SUBMODULE_ATTEMPTS,
    WorktreeBootstrapPreflight,
    clean_stale_git_locks,
    find_candidate_git_locks,
    is_git_process_active_in_repo,
    resolve_repo_pre_pr_entrypoint,
    run_worktree_bootstrap_preflight,
)
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

from staff import cli_version, workspace
from staff import quota as quota_mod
from staff.roles import RoleSpec

log = logging.getLogger("dashboard.staff.runner_ops")


class StaffRunPreparationError(RuntimeError):
    """Raised when a run is already marked failed with classification (do not overwrite in _worker)."""


_GITHUB_AUTH_REMEDIATION = (
    "On the fleet hub (hub-a), configure GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID, "
    "and GITHUB_APP_PRIVATE_KEY_FILE. On spoke nodes, ensure HUB_URL and HUB_FLEET_TOKEN "
    "are set so staff runs can mint tokens via POST /api/fleet/run-token on the hub. "
    "STAFF_ALLOW_OPERATOR_TOKEN=1 is hub-only and does not apply on fleet nodes."
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
    try:
        result = run_worktree_bootstrap_preflight(workdir, role=plan.role)
    except TypeError:
        result = run_worktree_bootstrap_preflight(workdir)
    if result.ok:
        if result.pre_pr_entrypoint:
            store.append_event(
                rec.id,
                "preflight",
                f"worktree bootstrap ok: python={result.python_executable}; pre_pr={result.pre_pr_entrypoint}",
            )
        elif result.pre_pr_advisory:
            store.append_event(
                rec.id,
                "preflight",
                (f"worktree bootstrap ok: python={result.python_executable}; pre_pr_advisory={result.pre_pr_advisory}"),
            )
        return result
    transition_to(
        store,
        rec.id,
        RunStatus.FAILED,
        failure_class=result.failure_class or "environment_error",
        retryable=result.retryable,
        remediation=result.remediation
        or "Worktree bootstrap preflight failed; fix environment prerequisites before re-dispatching.",
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


def _ensure_disk_floor_admitted(
    store: RunStore,
    rec: RunRecord,
    *,
    stage: str,
    allow_sweep: bool = True,
) -> bool:
    """Enforce ``resource_floor`` before ``stage``; returns whether a sweep ran."""
    if not stage.strip():
        raise ValueError("stage must be non-empty")
    swept = False
    breach = staff_disk_floor_breach_reason()
    if breach and allow_sweep:
        swept = True
        stats = workspace.sweep_stale_worktrees(store)
        if any(stats.values()):
            store.append_event(rec.id, "worktree", f"disk floor breached ({breach}); prune attempt {stats}")
        breach = staff_disk_floor_breach_reason()
    if breach:
        detail = f"runner schedule disk floor breached: {breach}; free space before {stage}"
        transition_to(
            store,
            rec.id,
            RunStatus.FAILED,
            failure_class="workspace_error",
            retryable=True,
            remediation="Free disk space until the runner-scheduler resource_floor readmits, then retry.",
            error=detail,
            ended_at=_now(),
        )
        store.append_event(rec.id, "error", detail)
        raise StaffRunPreparationError(detail)
    return swept


def prepare_run_workdir(store: RunStore, rec: RunRecord, plan: RunPlan) -> Path:
    """Resolve clone/worktree for ``rec``; persists ``workdir`` on the run row."""
    if not plan.repo:
        workdir = workspace.staff_worktrees_root() / rec.id
        workdir.mkdir(parents=True, exist_ok=True)
        store.update_run(rec.id, workdir=str(workdir))
        return workdir
    checkout = workspace.find_repo_checkout(plan.repo)
    swept = False
    if checkout is None:
        swept = _ensure_disk_floor_admitted(store, rec, stage=f"cloning {plan.repo}")
        store.append_event(rec.id, "clone", f"no local checkout of {plan.repo}; cloning")
        checkout = workspace.clone_repo(plan.repo)
    _ensure_disk_floor_admitted(
        store,
        rec,
        stage="creating a worktree",
        allow_sweep=not swept,
    )
    clean_stale_git_locks(checkout, main_root=checkout)
    worktree = workspace.staff_worktrees_root() / f"{plan.repo}-{rec.id}"
    workspace.add_run_worktree(checkout, worktree, plan.branch, plan.head_ref)
    clean_stale_git_locks(worktree, main_root=checkout)
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
        transition_to(
            store,
            rec.id,
            RunStatus.FAILED,
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
        transition_to(
            store,
            rec.id,
            RunStatus.FAILED,
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
    transition_to(
        store,
        rec.id,
        RunStatus.FAILED,
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


def apply_code_reviewer_post_classification(
    store: RunStore,
    rec: RunRecord,
    plan: RunPlan,
    rc: int,
    status: str,
    failure_class: str,
    retryable: bool,
    error: str,
    remediation: str,
    result_line: str,
    transcript: Path,
) -> tuple[str, str, bool, str, str]:
    from staff.verification import _target_pr_number

    from staff import review

    return review.maybe_finalize_code_reviewer_run(
        role=rec.role,
        rc=rc,
        store=store,
        run_id=rec.id,
        status=status,
        failure_class=failure_class,
        retryable=retryable,
        error=error,
        remediation=remediation,
        result_line=result_line,
        same_provider=review.is_same_provider_review(rec.prompt),
        repo=plan.repo or rec.repo,
        reviewer_model=str(plan.model or plan.provider or ""),
        expected_pr_number=_target_pr_number(rec),
    )


def ensure_preparing_before_launch(store: RunStore, rec: RunRecord) -> None:
    from staff.run_status import RunStatus, transition

    row = store.get_run(rec.id)
    if row is not None and row.status == "queued":
        transition(store, rec.id, RunStatus.QUEUED, RunStatus.PREPARING, started_at=_now())


def record_worker_crash(store: RunStore, rec: RunRecord, exc: BaseException) -> None:
    from staff import claim_guard

    transition_to(
        store,
        rec.id,
        RunStatus.FAILED,
        ended_at=_now(),
        error=str(exc)[:1000],
    )
    claim_guard.release_claim(rec.id, "failed")
    store.append_event(rec.id, "error", str(exc)[:1000])


def emit_terminal_run_status(
    store: RunStore,
    updated_rec: RunRecord,
    plan: RunPlan,
    *,
    status: str,
    question: str | None,
    result_line: str,
    cancelled: bool,
    rc: int,
    transcript: Path,
) -> None:
    from staff.failover_dispatch import run_completion_summary
    from staff.lessons import file_proposed_lessons
    from staff.run_link import handle_run_status_change, result_summary

    if status == "succeeded" and rc == 0 and not cancelled:
        file_proposed_lessons(store, updated_rec, transcript_path=transcript)
    q = question if status == "needs_input" else None
    if status == "succeeded":
        summary = run_completion_summary(store, updated_rec, result_line, provider=plan.provider, model=plan.model)
    else:
        summary = (updated_rec.outcome or result_summary(result_line)) or ""
    handle_run_status_change(updated_rec, status=status, question=q, summary=summary)
