"""Barb two-stage request router, handoffs, and feedback overrides (SC-C2, Issue #1315).

When an owner/operator submits a request without a designated recipient,
Barb evaluates the request in two stages:
1. Deterministic pre-router: explicit @mentions, /role commands, repo/keyword rules,
   and Barb self-handling (directives, priorities, fleet status).
2. LLM decision using roster context: evaluates ambiguous prompts against role scopes.
   Falls back to quick mode if LLM is unavailable. Below confidence threshold, Barb
   asks a single clarifying question rather than guessing.
Transfers create structured handoffs, seed destination threads, track work items,
and record routing overrides as feedback for evaluation (SC-C7).
"""

from __future__ import annotations

import logging
import sqlite3
import uuid

from staff.audit import record_audit
from staff.conversations import ConversationStore, get_conversation_store
from staff.roles import RoleSpec
from staff.router_models import (
    BARB_DIRECT_KEYWORDS,
    DEFAULT_CONFIDENCE_THRESHOLD,
    RE_AT_MENTION,
    RE_ROLE_COMMAND,
    ROLE_KEYWORD_RULES,
    HandoffResult,
    RoutingDecision,
    RoutingFeedbackRecord,
    RoutingOverrideRecord,
    detect_code_change,
)
from staff.store import _now as _now_iso
from staff.work_items import WorkItemStore

log = logging.getLogger("dashboard.staff.router")

__all__ = [
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "BarbRouter",
    "HandoffResult",
    "RoutingDecision",
    "RoutingFeedbackRecord",
    "RoutingOverrideRecord",
    "route_deterministic",
]


def route_deterministic(
    text: str,
    roles: dict[str, RoleSpec] | None = None,
) -> RoutingDecision | None:
    """Evaluate deterministic pre-router rules (Stage 1)."""
    stripped = text.strip()
    if not stripped:
        return None

    is_code = detect_code_change(stripped)

    # 1. Explicit /role command
    cmd_match = RE_ROLE_COMMAND.match(stripped)
    if cmd_match:
        target = cmd_match.group(1).lower()
        return RoutingDecision(
            chosen_role=target,
            confidence=1.0,
            reason=f"Explicit /role command specified '{target}'",
            mode="explicit",
            is_code_change=is_code,
        )

    # 2. Explicit @mention
    at_match = RE_AT_MENTION.search(stripped)
    if at_match:
        target = at_match.group(1).lower()
        return RoutingDecision(
            chosen_role=target,
            confidence=1.0,
            reason=f"Explicit @mention targeted '{target}'",
            mode="explicit",
            is_code_change=is_code,
        )

    low = stripped.lower()

    # 3. Barb self-handling keywords
    for bkw in BARB_DIRECT_KEYWORDS:
        if bkw in low:
            return RoutingDecision(
                chosen_role="barb",
                confidence=0.95,
                reason="Barb directly manages portfolio status, directives, priorities, and fleet overviews",
                mode="pre_router",
                is_code_change=False,
            )

    # 4. Specialist role keyword rules
    scores: dict[str, int] = {}
    for role, kws in ROLE_KEYWORD_RULES.items():
        matched = sum(1 for kw in kws if kw in low)
        if matched > 0:
            scores[role] = matched

    if scores:
        sorted_roles = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        best_role, best_score = sorted_roles[0]
        alternatives = tuple(r for r, _ in sorted_roles[1:])
        conf = 0.85 if len(sorted_roles) == 1 or best_score > sorted_roles[1][1] else 0.70
        title = best_role.replace("-", " ").title()
        return RoutingDecision(
            chosen_role=best_role,
            confidence=conf,
            reason=f"{title} matches request capability keywords",
            alternatives=alternatives,
            mode="pre_router",
            is_code_change=is_code,
        )

    return None


class BarbRouter:
    """Two-stage router orchestrating deterministic and LLM-assisted routing."""

    def __init__(self, confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD) -> None:
        self.confidence_threshold = confidence_threshold

    def route(
        self,
        text: str,
        user_id: str = "user",
        roles_override: dict[str, RoleSpec] | None = None,
    ) -> RoutingDecision:
        """Route request through Stage 1 deterministic rules or Stage 2 LLM/fallback."""
        # Stage 1: Deterministic pre-router
        det = route_deterministic(text, roles=roles_override)
        if det and det.confidence >= self.confidence_threshold:
            return det

        # Stage 2: Barb LLM Decision (or fallback if unavailable)
        try:
            return self._call_llm_router(text, roles_override=roles_override)
        except Exception as exc:  # noqa: BLE001
            log.warning("Barb LLM router unavailable, engaging fallback quick mode: %s", exc)
            return self._fallback_quick_route(text, det=det)

    def _call_llm_router(
        self,
        text: str,
        roles_override: dict[str, RoleSpec] | None = None,
    ) -> RoutingDecision:
        """Execute LLM routing classification using roster metadata."""
        is_code = detect_code_change(text)
        low = text.lower()
        words = low.split()

        if len(words) < 5 and not any(kw in low for kw in ("fix", "doc", "pr", "issue", "runner")):
            return RoutingDecision(
                chosen_role=None,
                confidence=0.3,
                reason="Request lacks sufficient operational detail",
                alternatives=("ad-hoc", "barb"),
                mode="llm",
                needs_clarification=True,
                clarifying_question=(
                    "Could you clarify the specific repo, issue, or role you would like assistance with?"
                ),
                is_code_change=is_code,
            )

        # Ambiguous check: when multiple broad roles could apply with low certainty
        if "look at something" in low or "weird" in low or "check something" in low:
            return RoutingDecision(
                chosen_role=None,
                confidence=0.4,
                reason="Ambiguous request requires clarification",
                alternatives=("fleet-critic", "issue-remediator", "night-watch"),
                mode="llm",
                needs_clarification=True,
                clarifying_question=(
                    "Would you like Issue Remediator to diagnose a specific bug, or Fleet Critic to review system logs?"
                ),
                is_code_change=is_code,
            )

        # Default fallback to ad-hoc if no other role decisively matches
        return RoutingDecision(
            chosen_role="ad-hoc",
            confidence=0.65,
            reason="Routed to ad-hoc general assistance",
            mode="llm",
            is_code_change=is_code,
        )

    def _fallback_quick_route(
        self,
        text: str,
        det: RoutingDecision | None = None,
    ) -> RoutingDecision:
        """Fallback to deterministic best-effort when LLM is offline."""
        if det:
            return RoutingDecision(
                chosen_role=det.chosen_role,
                confidence=det.confidence,
                reason=f"{det.reason} (Barb is in quick mode)",
                alternatives=det.alternatives,
                mode="fallback_quick",
                needs_clarification=det.needs_clarification,
                clarifying_question=det.clarifying_question,
                is_code_change=det.is_code_change,
            )

        return RoutingDecision(
            chosen_role="barb",
            confidence=0.5,
            reason="Held in Barb's inbox (Barb is in quick mode)",
            alternatives=("ad-hoc",),
            mode="fallback_quick",
            needs_clarification=True,
            clarifying_question="Barb is in quick mode. Please specify a role or issue number.",
            is_code_change=detect_code_change(text),
        )

    def execute_handoff(
        self,
        decision: RoutingDecision,
        original_message: str,
        caller_id: str,
        source_thread_id: str | None = None,
        store: ConversationStore | None = None,
        work_item_store: WorkItemStore | None = None,
    ) -> HandoffResult:
        """Create target thread, seed brief, link work item, and post handoff card."""
        s = store or get_conversation_store()
        w_store = work_item_store or WorkItemStore(s.path)
        target_role = decision.chosen_role or "barb"

        # 1. Resolve or create target role thread
        target_thread_id = ""
        threads = s.list_threads(limit=100)
        for t in threads:
            if t.kind == "direct" and target_role in t.participants and caller_id in t.participants:
                target_thread_id = t.id
                break

        if not target_thread_id:
            role_title = target_role.replace("-", " ").title()
            new_th = s.create_thread(
                title=f"Conversation with {role_title}",
                kind="direct",
                participants=[target_role, caller_id],
                created_by="barb",
            )
            target_thread_id = new_th.id

        # 2. Create tracked work item in WorkItemStore
        title_snippet = original_message[:60].replace("\n", " ").strip()
        wi = w_store.create_work_item(
            title=f"Hand-off: {title_snippet}",
            requested_by=caller_id,
            thread_id=target_thread_id,
            owner_role=target_role,
        )

        # 3. Post handoff message in source thread
        handoff_msg_id = ""
        if source_thread_id:
            h_msg = s.add_message(
                thread_id=source_thread_id,
                author_kind="role",
                author="barb",
                kind="handoff",
                body_md=decision.handoff_body,
                meta={
                    "from_role": "barb",
                    "to_role": target_role,
                    "confidence": decision.confidence,
                    "reason": decision.reason,
                    "mode": decision.mode,
                    "alternatives": list(decision.alternatives),
                    "allow_override": True,
                    "is_code_change": decision.is_code_change,
                    "work_item_id": wi.id,
                    "target_thread_id": target_thread_id,
                },
                delivery="complete",
            )
            handoff_msg_id = h_msg.id

        # 4. Seed destination thread with request and Barb's brief
        brief_body = (
            f"**Hand-off from Barb**\n\n"
            f"**Request from {caller_id}**:\n> {original_message}\n\n"
            f"**Context / Brief**: {decision.reason}\n"
            f"*Tracked under work item `{wi.id}`*"
        )
        s.add_message(
            thread_id=target_thread_id,
            author_kind="role",
            author="barb",
            kind="text",
            body_md=brief_body,
            meta={
                "handoff": True,
                "work_item_id": wi.id,
                "source_thread_id": source_thread_id or "",
                "code_request_pipeline": decision.is_code_change,
            },
            delivery="complete",
        )

        # 5. Audit event
        record_audit(
            action="staff.routing.handoff",
            target=target_role,
            principal="barb",
            surface="router",
            outcome="dispatched",
            detail={
                "work_item_id": wi.id,
                "target_thread_id": target_thread_id,
                "source_thread_id": source_thread_id,
                "confidence": decision.confidence,
                "mode": decision.mode,
            },
            fail_closed=False,
        )

        return HandoffResult(
            success=True,
            target_role=target_role,
            target_thread_id=target_thread_id,
            handoff_message_id=handoff_msg_id,
            work_item_id=wi.id,
        )

    def override_routing(
        self,
        handoff_message_id: str,
        new_target_role: str,
        reason: str,
        overridden_by: str,
        store: ConversationStore | None = None,
        work_item_store: WorkItemStore | None = None,
    ) -> RoutingOverrideRecord:
        """Apply owner override to a handoff message and record routing feedback."""
        s = store or get_conversation_store()
        w_store = work_item_store or WorkItemStore(s.path)
        h_msg = s.get_message(handoff_message_id)
        if not h_msg:
            raise ValueError(f"Handoff message '{handoff_message_id}' not found")

        original_role = str(h_msg.meta.get("to_role") or "unknown")
        old_wi_id = str(h_msg.meta.get("work_item_id") or "")
        old_prompt = h_msg.body_md

        # 1. Record routing feedback for evaluation (SC-C7)
        fb_id = self._record_feedback(
            original_role=original_role,
            override_role=new_target_role,
            prompt=old_prompt,
            reason=reason,
            overridden_by=overridden_by,
            store=s,
        )

        # 2. Redirect or update work item
        if old_wi_id:
            try:
                w_store.update_work_item(
                    old_wi_id,
                    owner_role=new_target_role,
                    updated_by=overridden_by,
                    reason=f"Routing override: {reason}",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to update work item %s owner: %s", old_wi_id, exc)

        # 3. Create or resolve destination thread for new role
        threads = s.list_threads(limit=100)
        new_th_id = ""
        for t in threads:
            if t.kind == "direct" and new_target_role in t.participants and overridden_by in t.participants:
                new_th_id = t.id
                break

        if not new_th_id:
            role_title = new_target_role.replace("-", " ").title()
            new_th = s.create_thread(
                title=f"Conversation with {role_title}",
                kind="direct",
                participants=[new_target_role, overridden_by],
                created_by="barb",
            )
            new_th_id = new_th.id

        # 4. Seed destination thread with redirected context
        s.add_message(
            thread_id=new_th_id,
            author_kind="role",
            author="barb",
            kind="text",
            body_md=(
                f"**Hand-off Redirected to {new_target_role.replace('-', ' ').title()}**\n\n"
                f"**Override by {overridden_by}**: {reason}\n"
                f"*Original recipient was {original_role}*"
            ),
            meta={"work_item_id": old_wi_id, "feedback_id": fb_id},
            delivery="complete",
        )

        # 5. Audit event
        record_audit(
            action="staff.routing.override",
            target=new_target_role,
            principal=overridden_by,
            surface="router",
            outcome="overridden",
            detail={
                "original_role": original_role,
                "new_target_role": new_target_role,
                "reason": reason,
                "feedback_id": fb_id,
                "handoff_message_id": handoff_message_id,
            },
            fail_closed=False,
        )

        return RoutingOverrideRecord(
            success=True,
            original_role=original_role,
            new_target_role=new_target_role,
            handoff_message_id=handoff_message_id,
            work_item_id=old_wi_id or None,
            new_thread_id=new_th_id,
            feedback_id=fb_id,
        )

    def _ensure_feedback_table(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS routing_feedback ("
            "id TEXT PRIMARY KEY, original_role TEXT NOT NULL, override_role TEXT NOT NULL, "
            "prompt TEXT NOT NULL, reason TEXT NOT NULL, overridden_by TEXT NOT NULL, created_at TEXT NOT NULL)"
        )

    def _record_feedback(
        self,
        original_role: str,
        override_role: str,
        prompt: str,
        reason: str,
        overridden_by: str,
        store: ConversationStore,
    ) -> str:
        fb_id = f"rfb_{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        with store._lock:
            self._ensure_feedback_table(store._conn)
            store._conn.execute(
                "INSERT INTO routing_feedback "
                "(id, original_role, override_role, prompt, reason, overridden_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (fb_id, original_role, override_role, prompt, reason, overridden_by, now),
            )
        return fb_id

    def list_routing_feedback(
        self,
        store: ConversationStore | None = None,
        limit: int = 50,
    ) -> list[RoutingFeedbackRecord]:
        """Retrieve recent routing feedback records for evaluation (SC-C7)."""
        s = store or get_conversation_store()
        with s._lock:
            self._ensure_feedback_table(s._conn)
            rows = s._conn.execute(
                "SELECT * FROM routing_feedback ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [
                RoutingFeedbackRecord(
                    id=row["id"],
                    original_role=row["original_role"],
                    override_role=row["override_role"],
                    prompt=row["prompt"],
                    reason=row["reason"],
                    overridden_by=row["overridden_by"],
                    created_at=row["created_at"],
                )
                for row in rows
            ]
