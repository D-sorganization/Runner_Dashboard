"""Core pipeline and wave orchestrator for Code Request executor stage (CR-5, #1287).

Manages execution of planned child issues across dependency waves:
- Resolves executors (pinned profiles or cheapest in-budget providers).
- Coordinates via claim check, lease posting, and PR validation.
- Enforces repo-level concurrency caps per wave.
- Retries with updated handoffs; escalates tier after 2 failures.
- After strong tier failure, flags needs-human-triage and pauses that branch.
- Rolls status, child states, and costs back up to the Code Request.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from code_requests.executor_coordination import (
    can_dispatch_child,
    post_child_lease,
    release_child_lease,
    validate_pr_submission,
)
from code_requests.executor_models import (
    ChildExecutionRecord,
    ChildExecutionState,
    ChildIssuePayload,
    ExecutionConfig,
    ExecutorRollup,
)
from code_requests.executor_router import escalate_tier, resolve_child_executor
from code_requests.model import CodeRequest

log = logging.getLogger("dashboard.code_requests.executor_stage")


def compute_waves(children: list[ChildExecutionRecord | ChildIssuePayload]) -> list[list[str]]:
    """Partition child issues into ordered dependency waves (topological sort).

    Raises:
        ValueError: If a dependency cycle is detected or a dependency is unknown.
    """
    keys = {c.key for c in children}
    deps: dict[str, set[str]] = {}
    for c in children:
        local_deps = {d for d in c.dependencies if d in keys and d != c.key}
        deps[c.key] = local_deps

    waves: list[list[str]] = []
    resolved: set[str] = set()
    order = [c.key for c in children]

    while len(resolved) < len(order):
        wave = [k for k in order if k not in resolved and deps[k] <= resolved]
        if not wave:
            stuck = [k for k in order if k not in resolved]
            raise ValueError(f"dependency_cycle: unresolved cycle among {stuck}")
        waves.append(wave)
        resolved.update(wave)

    return waves


class ExecutorPipeline:
    """Stateful orchestrator for executing planned child issues of a Code Request."""

    def __init__(
        self,
        code_request_id: str,
        config: ExecutionConfig | None = None,
        *,
        session_id: str = "executor-pipeline",
        claim_check_fn: Callable[..., dict[str, Any]] | None = None,
        lease_poster_fn: Callable[..., dict[str, Any]] | None = None,
        lease_releaser_fn: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.code_request_id = code_request_id
        self.config = config or ExecutionConfig()
        self.session_id = session_id
        self.children: dict[str, ChildExecutionRecord] = {}
        self.waves: list[list[str]] = []
        self.current_wave_idx: int = 0
        self.paused_branches: set[str] = set()
        self.audit_log: list[str] = []
        self._claim_check_fn = claim_check_fn
        self._lease_poster_fn = lease_poster_fn
        self._lease_releaser_fn = lease_releaser_fn

    def initialize(
        self,
        payloads: list[ChildIssuePayload | ChildExecutionRecord],
        request: CodeRequest | None = None,
    ) -> None:
        """Initialize pipeline with planned child issues and compute waves."""
        self.waves = compute_waves(payloads)
        self.children.clear()
        self.paused_branches.clear()
        self.current_wave_idx = 0

        wave_map: dict[str, int] = {}
        for w_idx, wave_keys in enumerate(self.waves):
            for k in wave_keys:
                wave_map[k] = w_idx

        for p in payloads:
            rec = ChildExecutionRecord(
                key=p.key,
                repository=p.repository,
                title=p.title,
                issue_number=p.issue_number,
                dependencies=list(p.dependencies),
                tier=p.tier,
                task_class=p.task_class,
                complexity=p.complexity,
                turnover_doc=p.turnover_doc,
                handoff=getattr(p, "handoff", "") or p.turnover_doc,
                wave=wave_map.get(p.key, 0),
            )
            self.children[p.key] = rec

        self.audit_log.append(f"Initialized pipeline with {len(self.children)} children across {len(self.waves)} waves")

    def is_child_ready(self, key: str) -> bool:
        """Return True if a child is queued, unblocked, and all dependencies merged."""
        child = self.children.get(key)
        if not child or child.state != ChildExecutionState.QUEUED:
            return False
        if key in self.paused_branches:
            return False
        for dep in child.dependencies:
            dep_rec = self.children.get(dep)
            if dep_rec and dep_rec.state != ChildExecutionState.MERGED:
                return False
        return True

    def get_ready_children(self) -> list[ChildExecutionRecord]:
        """Return ready children in the current wave respecting concurrency cap."""
        if self.current_wave_idx >= len(self.waves):
            return []

        active_counts: dict[str, int] = defaultdict(int)
        for c in self.children.values():
            if c.state in (ChildExecutionState.CLAIMED, ChildExecutionState.PR_OPEN, ChildExecutionState.CI):
                active_counts[c.repository] += 1

        ready: list[ChildExecutionRecord] = []
        for key in self.waves[self.current_wave_idx]:
            child = self.children[key]
            if self.is_child_ready(key):
                if active_counts[child.repository] < self.config.max_concurrent_per_repo:
                    ready.append(child)
                    active_counts[child.repository] += 1
                else:
                    log.debug("Child %s deferred: repo %s at concurrency limit", key, child.repository)
        return ready

    def dispatch_child(
        self,
        key: str,
        request: CodeRequest | None = None,
        provider_registry: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Resolve executor, perform coordination checks, and mark child as claimed."""
        child = self.children.get(key)
        if not child:
            return False, f"child_not_found: {key}"
        if not self.is_child_ready(key):
            return False, f"child_not_ready: {key}"

        # 1. Resolve executor provider and tier
        provider, resolved_tier, route_reason = resolve_child_executor(
            child, request=request, provider_registry=provider_registry
        )
        child.agent = provider
        child.tier = resolved_tier

        # 2. Check claim
        can_dispatch, claim_reason = can_dispatch_child(child, provider, claim_check_fn=self._claim_check_fn)
        if not can_dispatch:
            self.audit_log.append(f"Child {key} dispatch skipped: {claim_reason}")
            return False, claim_reason

        # 3. Post lease
        ok, receipt = post_child_lease(
            child,
            provider,
            self.session_id,
            ttl=self.config.lease_ttl_seconds,
            lease_poster_fn=self._lease_poster_fn,
        )
        if not ok:
            self.audit_log.append(f"Child {key} lease failed: {receipt}")
            return False, receipt

        child.lease_receipt = receipt
        child.state = ChildExecutionState.CLAIMED
        self.audit_log.append(f"Dispatched {key} to {provider} ({resolved_tier.value}): {route_reason}")
        return True, f"dispatched to {provider}"

    def report_pr_opened(
        self,
        key: str,
        pr_number: int,
        pr_body: str = "",
        pr_labels: list[str] | None = None,
    ) -> tuple[bool, str]:
        """Record PR opened for a child issue and validate PR metadata."""
        child = self.children.get(key)
        if not child:
            return False, f"child_not_found: {key}"

        if pr_body and child.agent:
            valid, reason = validate_pr_submission(pr_body, pr_labels or [], child.issue_number, child.agent)
            if not valid:
                log.warning("PR %s metadata warning for child %s: %s", pr_number, key, reason)

        child.pr_number = pr_number
        child.state = ChildExecutionState.PR_OPEN
        self.audit_log.append(f"Child {key} opened PR #{pr_number}")
        return True, f"pr #{pr_number} recorded"

    def report_ci_status(self, key: str, status: str) -> None:
        """Update CI status for a child."""
        child = self.children.get(key)
        if child:
            child.pr_status = status
            if status.lower() in ("in_progress", "pending", "running"):
                child.state = ChildExecutionState.CI

    def report_merged(self, key: str, cost: float = 0.0) -> None:
        """Record successful PR merge and advance waves when current wave completes."""
        child = self.children.get(key)
        if not child:
            return

        child.state = ChildExecutionState.MERGED
        child.cost += max(0.0, cost)
        if child.agent:
            release_child_lease(
                child, child.agent, self.session_id, "merged", lease_releaser_fn=self._lease_releaser_fn
            )

        self.audit_log.append(f"Child {key} merged (cost: ${child.cost:.2f})")
        self._check_and_advance_wave()

    def report_failed(
        self,
        key: str,
        reason: str,
        cost: float = 0.0,
        updated_handoff: str = "",
    ) -> None:
        """Handle execution failure, retry, tier escalation, or human triage pausing."""
        child = self.children.get(key)
        if not child:
            return

        child.cost += max(0.0, cost)
        child.attempts += 1
        child.error_reasons.append(reason)
        if updated_handoff:
            child.handoff = updated_handoff

        if child.agent:
            release_child_lease(
                child,
                child.agent,
                self.session_id,
                f"failed: {reason}",
                lease_releaser_fn=self._lease_releaser_fn,
            )

        # Check if attempts exceed max per tier
        if child.attempts >= self.config.max_tier_attempts:
            next_tier = escalate_tier(child.tier)
            if next_tier is not None:
                old_tier = child.tier
                child.tier = next_tier
                child.attempts = 0  # reset for new tier
                child.escalation_history.append(f"escalated from {old_tier.value} to {next_tier.value}")
                child.state = ChildExecutionState.QUEUED
                self.audit_log.append(
                    f"Child {key} failed {self.config.max_tier_attempts}x at {old_tier.value}; "
                    f"escalated to {next_tier.value}"
                )
            else:
                # Strong tier failed twice -> human triage
                child.state = ChildExecutionState.PAUSED_FOR_HUMAN
                self.paused_branches.add(key)
                self._pause_dependent_branches(key)
                self.audit_log.append(f"Child {key} failed strong tier twice; branch paused with needs-human-triage")
        else:
            # Retry at current tier
            child.state = ChildExecutionState.QUEUED
            self.audit_log.append(
                f"Child {key} failed (attempt {child.attempts}/{self.config.max_tier_attempts}); queued for retry"
            )

    def _pause_dependent_branches(self, failed_key: str) -> None:
        """Block all downstream transitive dependents of a failed/paused child."""
        to_visit = [failed_key]
        while to_visit:
            curr = to_visit.pop(0)
            for k, c in self.children.items():
                if curr in c.dependencies and c.state != ChildExecutionState.MERGED:
                    c.state = ChildExecutionState.BLOCKED
                    self.paused_branches.add(k)
                    to_visit.append(k)
                    self.audit_log.append(f"Child {k} blocked by failed ancestor {curr}")

    def _check_and_advance_wave(self) -> None:
        """Advance to next wave if all children in the current wave are merged."""
        if not self.config.auto_advance_waves or self.current_wave_idx >= len(self.waves):
            return

        current_wave_keys = self.waves[self.current_wave_idx]
        if all(self.children[k].state == ChildExecutionState.MERGED for k in current_wave_keys):
            self.current_wave_idx += 1
            self.audit_log.append(
                f"Wave {self.current_wave_idx - 1} completed; advanced to wave {self.current_wave_idx}"
            )

    def get_rollup(self) -> ExecutorRollup:
        """Compute aggregate progress, costs, and terminal state."""
        all_children = list(self.children.values())
        total_cost = sum(c.cost for c in all_children)
        completed = [c.key for c in all_children if c.state == ChildExecutionState.MERGED]
        failed = [c.key for c in all_children if c.state == ChildExecutionState.PAUSED_FOR_HUMAN]
        blocked = [c.key for c in all_children if c.state == ChildExecutionState.BLOCKED]

        active_states = (ChildExecutionState.CLAIMED, ChildExecutionState.PR_OPEN, ChildExecutionState.CI)
        if len(completed) == len(all_children) and len(all_children) > 0:
            state = "done"
        elif self.paused_branches:
            state = "failed"
        elif any(c.state in active_states for c in all_children):
            state = "executing"
        else:
            state = "queued"

        return ExecutorRollup(
            code_request_id=self.code_request_id,
            state=state,
            total_cost=total_cost,
            waves=self.waves,
            current_wave=self.current_wave_idx,
            children=self.children,
            completed_children=completed,
            failed_children=failed,
            blocked_children=blocked,
            paused_branches=sorted(self.paused_branches),
            audit_log=list(self.audit_log),
        )
