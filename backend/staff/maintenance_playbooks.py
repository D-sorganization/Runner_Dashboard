"""Remediation playbooks and self-heal policy for the Maintenance role (SC-E5, Issue #1322).

Enforces Owner decisions on autonomous operations:
- Self-heal playbooks (restart wedged listener, cancel & rerun stale queued job, clean
  orphaned worktrees) auto-execute when low-risk.
- Rate-limited to max 3 self-heal actions per host per hour.
- Barb logs every action in the Maintenance conversation thread.
- Repeat failures on the same target escalate through Barb to operator review.
- Medium/high risk actions generate action proposals and tracked work items awaiting approval.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Any

from staff.actions import (
    ActionContext,
    ActionRiskClass,
)
from staff.audit import record_audit
from staff.conversations import (
    ConversationStore,
    ThreadRecord,
    get_conversation_store,
)
from staff.maintenance import execute_maintenance
from staff.work_items import WorkItemStore

if TYPE_CHECKING:
    from staff.maintenance_detect import DetectionIssue

log = logging.getLogger("dashboard.staff.maintenance_playbooks")

MAX_SELF_HEAL_PER_HOST_PER_HOUR = 3
THROTTLE_WINDOW_SECONDS = 3600.0  # 1 hour
MAINTENANCE_THREAD_TITLE = "Fleet Maintenance"


class SelfHealThrottleTracker:
    """Tracks self-heal actions to enforce 3/host/hour limits and detect repeat failures."""

    def __init__(self, window_seconds: float = THROTTLE_WINDOW_SECONDS) -> None:
        self._window_seconds = window_seconds
        self._lock = threading.Lock()
        # host -> list of execution timestamps
        self._host_actions: dict[str, list[float]] = {}
        # target -> list of execution timestamps
        self._target_actions: dict[str, list[float]] = {}

    def _prune(self, now: float) -> None:
        cutoff = now - self._window_seconds
        for host in list(self._host_actions.keys()):
            self._host_actions[host] = [ts for ts in self._host_actions[host] if ts >= cutoff]
            if not self._host_actions[host]:
                del self._host_actions[host]

        for target in list(self._target_actions.keys()):
            self._target_actions[target] = [ts for ts in self._target_actions[target] if ts >= cutoff]
            if not self._target_actions[target]:
                del self._target_actions[target]

    def can_self_heal(self, host: str, target: str, now: float | None = None) -> tuple[bool, str]:
        current_time = now if now is not None else time.time()
        with self._lock:
            self._prune(current_time)

            # Check repeat failure on the same target within window
            recent_target = self._target_actions.get(target, [])
            if recent_target:
                return False, "repeat_failure"

            # Check per-host rate limit
            host_history = self._host_actions.get(host, [])
            if len(host_history) >= MAX_SELF_HEAL_PER_HOST_PER_HOUR:
                return False, "host_rate_limit_exceeded"

            return True, ""

    def record_self_heal(self, host: str, target: str, action: str, now: float | None = None) -> None:
        current_time = now if now is not None else time.time()
        with self._lock:
            self._host_actions.setdefault(host, []).append(current_time)
            self._target_actions.setdefault(target, []).append(current_time)

    def reset(self) -> None:
        with self._lock:
            self._host_actions.clear()
            self._target_actions.clear()


_GLOBAL_THROTTLE = SelfHealThrottleTracker()


def get_self_heal_throttle() -> SelfHealThrottleTracker:
    return _GLOBAL_THROTTLE


def reset_self_heal_throttle() -> None:
    _GLOBAL_THROTTLE.reset()


def get_or_create_maintenance_thread(store: ConversationStore) -> ThreadRecord:
    """Retrieve or initialize the dedicated Fleet Maintenance conversation thread."""
    threads = store.list_threads()
    for t in threads:
        if t.title == MAINTENANCE_THREAD_TITLE or (t.meta and t.meta.get("role") == "maintenance"):
            return t

    return store.create_thread(
        title=MAINTENANCE_THREAD_TITLE,
        kind="direct",
        participants=["maintenance", "barb"],
        role="maintenance",
        created_by="system",
        meta={"system_purpose": "fleet_maintenance", "role": "maintenance"},
    )


def remediate_issue(
    issue: DetectionIssue,
    ctx: ActionContext,
    conversation_store: ConversationStore | None = None,
    work_item_store: WorkItemStore | None = None,
    throttle: SelfHealThrottleTracker | None = None,
) -> dict[str, Any]:
    """Execute remediation playbook for a detected issue according to safety policies."""
    conv_store = conversation_store or get_conversation_store()
    wi_store = work_item_store or WorkItemStore()
    th_tracker = throttle or get_self_heal_throttle()

    thread = get_or_create_maintenance_thread(conv_store)

    # 1. Low-risk Self-Heal branch
    if issue.self_heal_eligible:
        allowed, reason = th_tracker.can_self_heal(host=issue.host, target=issue.target)

        if allowed:
            # Execute self-heal operations
            if issue.suggested_action == "maintenance.runner_restart":
                execute_maintenance(
                    "maintenance.runner_restart",
                    {"runner_name": issue.target, "host": issue.host},
                    ctx,
                )
            elif issue.suggested_action == "maintenance.run_cancel":
                repo = str(issue.details.get("repo", ""))
                run_id = int(issue.details.get("run_id", 0))
                # Cancel stale run
                execute_maintenance("maintenance.run_cancel", {"repo": repo, "run_id": run_id}, ctx)
                # Re-run to unblock queue
                execute_maintenance(
                    "maintenance.run_rerun",
                    {"repo": repo, "run_id": run_id, "failed_only": False},
                    ctx,
                )
            elif issue.suggested_action == "maintenance.trim_worktrees":
                execute_maintenance("maintenance.trim_worktrees", {}, ctx)

            # Record in throttle tracker
            th_tracker.record_self_heal(host=issue.host, target=issue.target, action=issue.suggested_action)

            # Post Barb notification to Maintenance thread
            body_md = (
                f"🛠️ **[Self-Heal]** Barb auto-remediated `{issue.detector}` on `{issue.target}`.\n"
                f"- **Action:** `{issue.suggested_action}`\n"
                f"- **Host:** `{issue.host}`\n"
                f"- **Policy:** Autonomous self-heal within 3/host/hour quota."
            )
            msg = conv_store.add_message(
                thread_id=thread.id,
                author_kind="role",
                author="barb",
                kind="status",
                body_md=body_md,
            )

            # Record in work-item ledger (state=done via in_progress)
            wi = wi_store.create_work_item(
                title=f"Self-heal {issue.detector} on {issue.target}",
                requested_by="barb",
                thread_id=thread.id,
                owner_role="maintenance",
                links={"messages": [msg.id] if msg else []},
            )
            wi_store.transition_state(wi.id, "in_progress", actor="barb", reason="executing self-heal")
            wi = wi_store.transition_state(wi.id, "done", actor="barb", reason="self-heal complete")

            record_audit(
                action="maintenance",
                principal="barb",
                target=issue.target,
                thread_id=thread.id,
                detail={
                    "sub_action": "self_heal",
                    "detector": issue.detector,
                    "action": issue.suggested_action,
                    "host": issue.host,
                },
            )

            return {
                "status": "self_healed",
                "action": issue.suggested_action,
                "work_item_id": wi.id,
                "thread_id": thread.id,
            }

        # Throttled or Repeat Failure -> Escalate through Barb
        log.warning(
            "Self-heal throttled (%s) for %s on %s; escalating through Barb",
            reason,
            issue.detector,
            issue.target,
        )
        desc = (
            "Repeat failure on target within 1 hour"
            if reason == "repeat_failure"
            else "Exceeded 3 self-heals per host per hour limit"
        )
        escalation_md = (
            f"⚠️ **[Escalation]** Auto-remediation skipped ({reason}) for `{issue.detector}` on `{issue.target}`.\n"
            f"- **Host:** `{issue.host}`\n"
            f"- **Reason:** {desc}.\n"
            f"- **Action Required:** Operator review and manual approval."
        )
        msg = conv_store.add_message(
            thread_id=thread.id,
            author_kind="role",
            author="barb",
            kind="status",
            body_md=escalation_md,
        )
        msg_id = msg.id if msg else f"msg-{int(time.time())}"

        params = (
            {"runner_name": issue.target, "host": issue.host}
            if issue.suggested_action == "maintenance.runner_restart"
            else {"repo": issue.details.get("repo", ""), "run_id": issue.details.get("run_id", 0)}
        )

        proposal = conv_store.create_proposal(
            message_id=msg_id,
            thread_id=thread.id,
            action=issue.suggested_action,
            params=params,
            risk=ActionRiskClass.MEDIUM,
            principal="barb",
        )

        wi = wi_store.create_work_item(
            title=f"Escalated {issue.detector} on {issue.target}",
            requested_by="barb",
            thread_id=thread.id,
            owner_role="maintenance",
            links={"proposals": [proposal.id] if proposal else []},
        )
        wi = wi_store.transition_state(wi.id, "escalated", actor="barb", reason=reason)

        record_audit(
            action="maintenance",
            principal="barb",
            target=issue.target,
            thread_id=thread.id,
            detail={
                "sub_action": "escalated",
                "detector": issue.detector,
                "reason": reason,
                "proposal_id": proposal.id if proposal else None,
            },
        )

        return {
            "status": "escalated",
            "reason": reason,
            "proposal_id": proposal.id if proposal else None,
            "work_item_id": wi.id,
            "thread_id": thread.id,
        }

    # 2. Medium/High Risk Remediation -> Create Proposal for Approval
    proposal_md = (
        f"📋 **[Maintenance Proposal]** Issue detected: `{issue.detector}` on `{issue.target}`.\n"
        f"- **Severity:** `{issue.severity}`\n"
        f"- **Proposed Action:** `{issue.suggested_action}` ({issue.risk_class} risk)\n"
        f"- **Details:** {issue.details}\n"
        f"Awaiting operator/owner approval to execute."
    )
    msg = conv_store.add_message(
        thread_id=thread.id,
        author_kind="role",
        author="maintenance",
        kind="action_proposal",
        body_md=proposal_md,
    )
    msg_id = msg.id if msg else f"msg-{int(time.time())}"

    proposal = conv_store.create_proposal(
        message_id=msg_id,
        thread_id=thread.id,
        action=issue.suggested_action,
        params=issue.details,
        risk=issue.risk_class,
        principal="maintenance",
    )

    wi = wi_store.create_work_item(
        title=f"Remediate {issue.detector} on {issue.target}",
        requested_by="maintenance",
        thread_id=thread.id,
        owner_role="maintenance",
        links={"proposals": [proposal.id] if proposal else []},
    )

    record_audit(
        action="maintenance",
        principal="maintenance",
        target=issue.target,
        thread_id=thread.id,
        detail={
            "sub_action": "proposed",
            "detector": issue.detector,
            "risk_class": issue.risk_class,
            "proposal_id": proposal.id if proposal else None,
        },
    )

    return {
        "status": "proposed",
        "proposal_id": proposal.id if proposal else None,
        "work_item_id": wi.id,
        "thread_id": thread.id,
    }
