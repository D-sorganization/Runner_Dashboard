"""Remediation playbooks and self-heal engine for Maintenance role (SC-E5, Issue #1322).

Owner Policy:
Only Self-heal playbooks run automatically:
1. Restart one wedged listener.
2. Cancel and re-run one stale queued job.
3. Clean orphaned worktrees.
Rate-limited to <= 3 per host per hour; repeat failures escalate through Barb.
All other detections become ActionProposals presented by Barb.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from identity import Principal
from staff.actions import ACTION_REGISTRY, ActionContext, ActionResult
from staff.audit import record_audit
from staff.conversations import ConversationStore, get_conversation_store
from staff.work_items import WorkItemStore, get_work_item_store

if TYPE_CHECKING:
    from staff.maintenance_detect import Detection

log = logging.getLogger("dashboard.staff.maintenance_playbooks")


@dataclass
class RemediationResult:
    """Outcome of evaluating or executing a remediation playbook."""

    executed: bool
    status: str  # "auto_healed", "proposed", "escalated", "failed"
    work_item_id: str | None = None
    proposal_id: str | None = None
    thread_id: str = ""
    reason: str = ""
    action_result: ActionResult | None = None


class AutoHealRateTracker:
    """Enforces <= 3 auto-heals per host per hour and tracks repeat target failures."""

    def __init__(self, max_per_host_per_hour: int = 3, window_seconds: float = 3600.0) -> None:
        self.max_per_host = max_per_host_per_hour
        self.window = window_seconds
        self._lock = threading.Lock()
        self._host_history: dict[str, list[float]] = {}
        self._target_history: dict[tuple[str, str, str], list[tuple[float, bool]]] = {}

    def can_auto_heal(self, host: str, target: str, action: str, now: float | None = None) -> tuple[bool, str]:
        cur = now if now is not None else time.time()
        with self._lock:
            cutoff = cur - self.window
            runs = [t for t in self._host_history.get(host, []) if t > cutoff]
            self._host_history[host] = runs
            if len(runs) >= self.max_per_host:
                return (
                    False,
                    f"hourly host self-heal budget exceeded ({len(runs)}/{self.max_per_host})",
                )

            target_runs = [(t, s) for (t, s) in self._target_history.get((host, target, action), []) if t > cutoff]
            self._target_history[(host, target, action)] = target_runs
            failed = [t for (t, s) in target_runs if not s]
            if failed:
                return (
                    False,
                    f"repeat self-heal failure on {target} ({len(failed)} failure(s) in past hour)",
                )
            return True, ""

    def record_attempt(
        self,
        host: str,
        target: str,
        action: str,
        success: bool,
        ts: float | None = None,
    ) -> None:
        cur = ts if ts is not None else time.time()
        with self._lock:
            self._host_history.setdefault(host, []).append(cur)
            self._target_history.setdefault((host, target, action), []).append((cur, success))

    def reset(self) -> None:
        with self._lock:
            self._host_history.clear()
            self._target_history.clear()


class MaintenancePlaybookEngine:
    """Executes low-risk self-heal playbooks and converts other actions to proposals."""

    def __init__(
        self,
        conversation_store: ConversationStore | None = None,
        work_item_store: WorkItemStore | None = None,
        rate_tracker: AutoHealRateTracker | None = None,
        default_thread_id: str | None = None,
    ) -> None:
        self.conv_store = conversation_store or get_conversation_store()
        self.work_store = work_item_store or get_work_item_store()
        self.tracker = rate_tracker or AutoHealRateTracker()
        self.default_thread_id = default_thread_id

    def _get_or_create_thread(self) -> str:
        if self.default_thread_id:
            return self.default_thread_id
        th = self.conv_store.create_thread(
            title="Fleet Maintenance & Self-Heal",
            kind="auto",
            participants=["barb", "operator"],
        )
        self.default_thread_id = th.id
        return th.id

    def remediate(self, detection: Detection) -> RemediationResult:
        thread_id = self._get_or_create_thread()

        if detection.auto_heal_eligible:
            allowed, escalate_reason = self.tracker.can_auto_heal(
                detection.host, detection.target, detection.recommended_action
            )
            if allowed:
                return self._execute_self_heal(detection, thread_id)
            return self._propose_escalation(detection, thread_id, reason=f"Escalated by Barb: {escalate_reason}")

        return self._propose_escalation(
            detection,
            thread_id,
            reason=f"Proposed by Barb: {detection.kind} on {detection.target}",
        )

    def _create_proposal(self, det: Detection, thread_id: str, reason: str, msg_md: str) -> Any:
        msg = self.conv_store.add_message(
            thread_id=thread_id,
            author_kind="role",
            author="barb",
            kind="action_proposal",
            body_md=msg_md,
            meta={"detection": det.kind, "severity": det.severity},
        )
        risk = "high" if det.severity in ("high", "critical") else "medium"
        prop = self.conv_store.create_proposal(
            message_id=msg.id,
            thread_id=thread_id,
            action=det.recommended_action,
            params=det.action_params,
            risk=risk,
            principal="barb",
        )
        with self.conv_store._lock:  # noqa: SLF001
            self.conv_store._conn.execute(  # noqa: SLF001
                "UPDATE action_proposals SET reason = ? WHERE id = ?", (reason, prop.id)
            )
            self.conv_store._conn.commit()  # noqa: SLF001
        return prop

    def _execute_self_heal(self, det: Detection, thread_id: str) -> RemediationResult:
        action_def = ACTION_REGISTRY.get(det.recommended_action)
        if not action_def:
            raise ValueError(f"Action '{det.recommended_action}' not registered in ACTION_REGISTRY")

        wi = self.work_store.create_work_item(
            title=f"Self-heal: {det.recommended_action} on {det.target}",
            requested_by="barb",
            owner_role="maintenance",
            thread_id=thread_id,
        )
        self.work_store.transition_state(wi.id, "in_progress", actor="barb", reason="Executing self-heal playbook")
        ctx = ActionContext(
            thread_id=thread_id,
            proposing_role="maintenance",
            caller=Principal(id="barb", name="Barb", type="agent", roles=["maintenance"]),
        )
        res = action_def.executor(det.action_params, ctx)
        self.tracker.record_attempt(det.host, det.target, det.recommended_action, success=res.success)

        if res.success:
            self.work_store.transition_state(wi.id, "done", actor="barb", reason="Self-heal executed successfully")
            self.conv_store.add_message(
                thread_id=thread_id,
                author_kind="role",
                author="barb",
                kind="status",
                body_md=f"**Self-Heal Executed**: Applied `{det.recommended_action}` on `{det.target}`.",
            )
            return RemediationResult(
                executed=True,
                status="auto_healed",
                work_item_id=wi.id,
                thread_id=thread_id,
                action_result=res,
            )

        self.work_store.transition_state(
            wi.id,
            "escalated",
            actor="barb",
            reason=f"Self-heal execution failed: {res.error}",
        )
        msg_md = f"**Self-Heal Failed & Escalated**: `{det.recommended_action}` failed on `{det.target}`: {res.error}."
        prop = self._create_proposal(det, thread_id, res.error or "Execution failed", msg_md)
        return RemediationResult(
            executed=True,
            status="escalated",
            work_item_id=wi.id,
            proposal_id=prop.id,
            thread_id=thread_id,
            reason=res.error or "Execution failed",
            action_result=res,
        )

    def _propose_escalation(self, det: Detection, thread_id: str, reason: str) -> RemediationResult:
        wi = self.work_store.create_work_item(
            title=f"Maintenance Proposal: {det.recommended_action} on {det.target}",
            requested_by="barb",
            owner_role="maintenance",
            thread_id=thread_id,
        )
        self.work_store.transition_state(wi.id, "in_progress", actor="barb", reason="Evaluating playbook")
        self.work_store.transition_state(wi.id, "waiting_on_user", actor="barb", reason=reason)
        msg_md = f"**Maintenance Proposal**: `{det.recommended_action}` on `{det.target}`.\n\n*Reason*: {reason}"
        prop = self._create_proposal(det, thread_id, reason, msg_md)

        record_audit(
            action="maintenance_proposal",
            target=det.target,
            principal="barb",
            surface="thread",
            thread_id=thread_id,
            outcome="success",
            detail={"detection": det.kind, "reason": reason, "proposal_id": prop.id},
            fail_closed=True,
            store=self.conv_store._audit_store,  # noqa: SLF001
        )
        return RemediationResult(
            executed=False,
            status="proposed",
            work_item_id=wi.id,
            proposal_id=prop.id,
            thread_id=thread_id,
            reason=reason,
        )
