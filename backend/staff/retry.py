"""Bounded retry policy and backoff calculation for staff runs (SC-A7, Issue #1303).

Scope:
- Retry only retryable classes (rate_limited, provider_error, workspace_error)
  with exponential backoff and jitter.
- Never retry needs_input, auth_expired, lease_blocked, cli_missing.
- Max attempts per role (default 2); each attempt is a new run linked by retry_of.
- Provider fallback chain per role (e.g. claude -> codex) used after retries
  are exhausted, recorded on the run.
- Retries count against the role's budget; the budget guard can stop them.
"""

from __future__ import annotations

import logging
import os
import random
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from staff.adapters import READ_ONLY_RUN_PROVIDERS
from staff.runner_ops import can_run_unattended, read_only_kwargs

if TYPE_CHECKING:
    from staff.budget import BudgetGuard
    from staff.holds import HoldsList
    from staff.plan import RunPlan
    from staff.roles import RoleSpec
    from staff.store import RunRecord

log = logging.getLogger("dashboard.staff.retry")

RETRYABLE_FAILURE_CLASSES = frozenset(
    {
        "rate_limited",
        "provider_error",
        "workspace_error",
    }
)

NON_RETRYABLE_FAILURE_CLASSES = frozenset(
    {
        "needs_input",
        "auth_expired",
        "lease_blocked",
        "cli_missing",
        "cli_outdated",
        "orphaned",
        "unkillable",
        "timeout",
        "stalled",
        "unknown",
        "no_result",
    }
)

DEFAULT_BASE_BACKOFF_SECONDS = float(os.environ.get("STAFF_RETRY_BASE_SECONDS", "2.0"))
DEFAULT_MAX_BACKOFF_SECONDS = float(os.environ.get("STAFF_RETRY_MAX_SECONDS", "300.0"))


def compute_backoff(
    attempt: int,
    base_seconds: float | None = None,
    max_seconds: float | None = None,
    jitter: bool = True,
) -> float:
    """Calculate exponential backoff with optional jitter.

    Pre: attempt >= 1.
    Post: returned delay >= 0.0 and <= max_seconds + 1.0.
    """
    base = base_seconds if base_seconds is not None else float(os.environ.get("STAFF_RETRY_BASE_SECONDS", "2.0"))
    cap = max_seconds if max_seconds is not None else float(os.environ.get("STAFF_RETRY_MAX_SECONDS", "300.0"))
    if base <= 0:
        return 0.0

    exp = min(cap, base * (2.0 ** max(0, attempt - 1)))
    jitter_val = random.uniform(0.0, min(1.0, exp * 0.25)) if jitter else 0.0  # noqa: S311
    return float(min(cap, exp + jitter_val))


def compute_next_attempt_at(delay_seconds: float) -> str:
    """Return UTC ISO-8601 string for next attempt after delay_seconds."""
    next_time = datetime.now(UTC) + timedelta(seconds=max(0.0, delay_seconds))
    return next_time.isoformat().replace("+00:00", "Z")


def is_retryable_class(failure_class: str) -> bool:
    """Return True if failure_class is eligible for bounded retry."""
    return failure_class in RETRYABLE_FAILURE_CLASSES and failure_class not in NON_RETRYABLE_FAILURE_CLASSES


def should_retry(
    rec: RunRecord,
    role: RoleSpec,
    budget_guard: BudgetGuard | None = None,
) -> tuple[bool, str]:
    """Determine whether a failed run is eligible to retry on the same provider.

    Returns (ok, reason).
    """
    if not is_retryable_class(rec.failure_class):
        return False, f"failure class '{rec.failure_class}' is not retryable"

    max_att = getattr(rec, "max_attempts", 2) or getattr(role, "max_attempts", 2) or 2
    if rec.attempt >= max_att:
        return False, f"retries exhausted ({rec.attempt}/{max_att})"

    if budget_guard is not None:
        can_run, reason = budget_guard.can_run(role)
        if not can_run:
            return False, f"daily budget reached: {reason}"

    return True, "retry allowed"


def next_fallback_provider(
    role: RoleSpec,
    current_provider: str,
    usable: Callable[[str], bool] = lambda _pid: True,
) -> str | None:
    """Return the next usable provider in the role's fallback chain, or None if exhausted.

    ``usable`` filters out providers that cannot take the run (e.g. chat-only, #1586).
    """
    fallback_chain = getattr(role, "fallback_providers", ())
    if fallback_chain:
        for p in fallback_chain:
            if p != current_provider and usable(p):
                return p
        return None

    providers = list(role.providers)
    if current_provider in providers:
        for p in providers[providers.index(current_provider) + 1 :]:
            if usable(p):
                return p

    return None


def handle_post_execution_retry(
    runner: Any,
    rec: RunRecord,
    plan: Any,
) -> bool:
    """Evaluate and dispatch retry or provider fallback after a run attempt concludes.

    Pre: rec and plan are valid.
    Post: returns True if a retry or fallback was scheduled, False otherwise.
    """
    store = runner.store
    latest = store.get_run(rec.id)
    if latest is None:
        return False

    # If this attempt succeeded and it was a retry attempt:
    if latest.status == "succeeded":
        if latest.retry_of:
            store.update_run(
                latest.retry_of,
                status="succeeded",
                ended_at=latest.ended_at,
                outcome=latest.outcome,
                error="",
            )
            store.append_event(latest.retry_of, "retry_success", f"attempt {latest.attempt} succeeded")
        return False

    if latest.status != "failed" or rec.id in runner._cancel_flags:
        return False

    role = runner.roles().get(plan.role)
    if role is None:
        return False

    from staff.budget import BudgetGuard
    from staff.runner import RunPlan
    from staff.store import RunRecord

    guard = BudgetGuard(store)
    root_id = latest.retry_of or latest.id

    # 1. Try retry on the same provider
    can_retry, reason = should_retry(latest, role, budget_guard=guard)
    if can_retry:
        delay = compute_backoff(latest.attempt)
        iso_next = compute_next_attempt_at(delay)
        store.update_run(latest.id, next_attempt_at=iso_next)
        if root_id != latest.id:
            store.update_run(root_id, next_attempt_at=iso_next)

        next_attempt = latest.attempt + 1
        attempt_id = attempt_run_id(root_id, next_attempt)
        next_branch = f"{plan.branch}-retry-{next_attempt}"
        attempt_plan = RunPlan(
            role=plan.role,
            provider=plan.provider,
            model=plan.model,
            repo=plan.repo,
            target_kind=plan.target_kind,
            target_ref=plan.target_ref,
            operator_prompt=plan.operator_prompt,
            prompt=plan.prompt,
            argv=plan.argv,
            branch=next_branch,
            lease_ritual=plan.lease_ritual,
            consolidation=plan.consolidation,
            focus=plan.focus,
        )
        attempt_rec = RunRecord(
            id=attempt_id,
            role=plan.role,
            provider=plan.provider,
            model=plan.model,
            machine=runner.machine,
            repo=plan.repo,
            target_kind=plan.target_kind,
            target_ref=plan.target_ref,
            prompt=plan.prompt,
            requested_by=latest.requested_by,
            on_behalf_of=latest.on_behalf_of,
            branch=next_branch,
            strategy_mode=plan.strategy_mode,
            retry_of=root_id,
            attempt=next_attempt,
            max_attempts=latest.max_attempts,
            next_attempt_at=iso_next,
            **_provenance(latest),
        )
        if not _claim(store, attempt_rec):
            return False
        retry_msg = (
            f"scheduled attempt {next_attempt}/{latest.max_attempts} "
            f"after {delay:.2f}s backoff (reason: {latest.failure_class})"
        )
        store.append_event(root_id, "retry", retry_msg)
        _schedule(runner, attempt_rec, attempt_plan, delay)
        return True

    # 2. If same provider retries exhausted, check fallback chain
    if is_retryable_class(latest.failure_class):
        fallback = next_fallback_provider(
            role,
            plan.provider,
            usable=lambda pid: (
                can_run_unattended(runner._adapters, pid)
                and (not role.code_read_only or pid in READ_ONLY_RUN_PROVIDERS)
            ),
        )
        if fallback:
            can_run, _ = guard.can_run(role)
            if can_run:
                next_attempt = latest.attempt + 1
                attempt_id = attempt_run_id(root_id, next_attempt)
                next_branch = f"{plan.branch}-fallback-{next_attempt}"
                argv = runner._adapters[fallback].build_command(
                    plan.prompt, "<workdir>", plan.model, **read_only_kwargs(role)
                )
                fallback_plan = RunPlan(
                    role=plan.role,
                    provider=fallback,
                    model=plan.model,
                    repo=plan.repo,
                    target_kind=plan.target_kind,
                    target_ref=plan.target_ref,
                    operator_prompt=plan.operator_prompt,
                    prompt=plan.prompt,
                    argv=argv,
                    branch=next_branch,
                    lease_ritual=plan.lease_ritual,
                    consolidation=plan.consolidation,
                    focus=plan.focus,
                )
                fallback_rec = RunRecord(
                    id=attempt_id,
                    role=plan.role,
                    provider=fallback,
                    model=plan.model,
                    machine=runner.machine,
                    repo=plan.repo,
                    target_kind=plan.target_kind,
                    target_ref=plan.target_ref,
                    prompt=plan.prompt,
                    requested_by=latest.requested_by,
                    on_behalf_of=latest.on_behalf_of,
                    branch=next_branch,
                    strategy_mode=plan.strategy_mode,
                    retry_of=root_id,
                    attempt=next_attempt,
                    max_attempts=latest.max_attempts + 1,
                    fallback_provider=fallback,
                    **_provenance(latest),
                )
                if not _claim(store, fallback_rec):
                    return False
                store.append_event(
                    root_id,
                    "fallback",
                    f"retries exhausted on {plan.provider}; falling back to {fallback}",
                )
                _schedule(runner, fallback_rec, fallback_plan, 0.0)
                return True

    return False


def _schedule(runner: Any, rec: RunRecord, plan: Any, delay: float) -> None:
    import threading

    if delay > 0.05:
        timer = threading.Timer(delay, runner._worker, args=(rec, plan))
        timer.daemon = True
        timer.name = f"staff-retry-{rec.id}"
        timer.start()
    else:
        thread = threading.Thread(
            target=runner._worker,
            args=(rec, plan),
            daemon=True,
            name=f"staff-retry-{rec.id}",
        )
        thread.start()


def attempt_run_id(root_id: str, attempt: int) -> str:
    """The id of attempt ``attempt`` of the run rooted at ``root_id`` (#1797).

    Deterministic, so every path that retries one run (post-execution, follow-up
    sweeps, a restart) races on the same primary key and exactly one attempt launches.
    """
    return f"{root_id}-a{attempt}"


def _provenance(rec: RunRecord) -> dict[str, Any]:
    return {"thread_id": rec.thread_id, "work_item_id": rec.work_item_id, "origin_node": rec.origin_node}


def _claim(store: Any, rec: RunRecord) -> bool:
    """Insert the attempt row; False when another path already claimed this attempt."""
    try:
        store.create_run(rec)
    except sqlite3.IntegrityError:
        log.info("retry attempt %s already claimed", rec.id)
        return False
    return True


@dataclass(frozen=True)
class RetryDecision:
    """What :func:`launch_retry` did: ``launched`` with the attempt's ``run_id``, or why not."""

    launched: bool
    run_id: str = ""
    reason: str = ""


def retry_plan(runner: Any, rec: RunRecord, role: RoleSpec) -> RunPlan:
    """Re-run ``rec`` as stored: its composed prompt, provider, model and target."""
    from staff.plan import RunPlan

    adapter = runner._adapters[rec.provider]
    base_branch = rec.branch or f"staff/{rec.role}-task"
    return RunPlan(
        role=rec.role,
        provider=rec.provider,
        model=rec.model,
        repo=rec.repo,
        target_kind=rec.target_kind,
        target_ref=rec.target_ref,
        operator_prompt="",
        prompt=rec.prompt,
        argv=adapter.build_command(rec.prompt, "<workdir>", rec.model, **read_only_kwargs(role)),
        branch=f"{base_branch}-retry-{rec.attempt + 1}",
        lease_ritual=bool(role.permissions.get("lease", True)) and rec.target_kind == "issue" and bool(rec.repo),
        **_provenance(rec),
    )


def launch_retry(
    runner: Any,
    failed: RunRecord,
    *,
    source: str,
    holds: HoldsList,
    allow_classes: frozenset[str] = frozenset(),
) -> RetryDecision:
    """Start the next attempt of a failed run on the runner's worker, at most once (#1797).

    Pre: ``failed`` names a run on ``runner``'s store.
    Post: ``launched`` means a queued attempt row exists with thread, work-item and
    origin provenance and its worker has been started. The class, attempt and budget
    gates are :func:`should_retry`'s (``allow_classes`` widens only the class gate), and
    an active schedule hold on the role refuses. A second caller for the same attempt
    gets ``reason="already claimed"``.
    """
    from staff.budget import BudgetGuard
    from staff.store import RunRecord

    store = runner.store
    latest = store.get_run(failed.id)
    if latest is None or latest.status != "failed":
        return RetryDecision(False, reason=f"run {failed.id} is {latest.status if latest else 'missing'}")
    role = runner.roles().get(latest.role)
    if role is None or latest.provider not in runner._adapters:
        return RetryDecision(False, reason=f"role {latest.role} or provider {latest.provider} is not available")
    gated = replace(latest, failure_class="provider_error") if latest.failure_class in allow_classes else latest
    ok, reason = should_retry(gated, role, budget_guard=BudgetGuard(store))
    if not ok:
        return RetryDecision(False, reason=reason)
    hold = holds.blocking(role.name, latest.repo)
    if hold is not None:
        return RetryDecision(False, reason=f"hold: {hold.text}")

    root_id = latest.retry_of or latest.id
    attempt = latest.attempt + 1
    plan = retry_plan(runner, latest, role)
    rec = RunRecord(
        id=attempt_run_id(root_id, attempt),
        role=latest.role,
        provider=latest.provider,
        model=latest.model,
        machine=runner.machine,
        repo=latest.repo,
        target_kind=latest.target_kind,
        target_ref=latest.target_ref,
        prompt=latest.prompt,
        requested_by=latest.requested_by,
        on_behalf_of=latest.on_behalf_of,
        branch=plan.branch,
        retry_of=root_id,
        attempt=attempt,
        max_attempts=latest.max_attempts,
        **_provenance(latest),
    )
    if not _claim(store, rec):
        return RetryDecision(False, run_id=rec.id, reason="already claimed")
    store.append_event(root_id, "retry", f"{source}: attempt {attempt}/{latest.max_attempts} ({latest.failure_class})")
    runner.launch(rec, plan)
    return RetryDecision(True, run_id=rec.id, reason="retry launched")
