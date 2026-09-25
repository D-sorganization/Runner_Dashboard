"""Waiting on You Inbox aggregation and Barb Briefings (SC-C5, Issue #1328).

Aggregates 6 key sources of work awaiting owner intervention:
1. Pending approvals (ActionProposalRecord state='proposed')
2. Needs-input questions (runs/messages requiring user input)
3. Escalations (runs with stalled/critical failure classes)
4. Project decisions needed (docs/project/STATUS.md via projects.service)
5. Board proposals awaiting owner (WorkItems with owner_role='board_secretary')
6. Auth sign-ins required (classifier auth_expired attention items)

Provides:
- Graceful degradation (isolated try/except per source with status 'ok' | 'unavailable')
- Structured markdown briefings generated for Barb (morning, evening, on-demand)
- Automatic thread posting and push dispatch for escalations
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from staff.classifier import format_attention_items
from staff.conversations import (
    ConversationStore,
    MessageRecord,
    get_conversation_store,
)
from staff.store import RunStore, get_store
from staff.work_items import WorkItemStore, get_work_item_store

log = logging.getLogger("dashboard.staff.inbox")


@dataclass(frozen=True)
class WaitingOnYouItem:
    """One action item waiting on owner or operator intervention."""

    id: str
    category: str  # approval | question | escalation | project_decision | board_proposal | auth_signin | thread
    title: str
    summary: str
    source: str
    severity: str = "medium"  # "critical" | "high" | "medium" | "low"
    action_url: str | None = None
    thread_id: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _collect_approvals(conv_store: ConversationStore) -> list[WaitingOnYouItem]:
    items: list[WaitingOnYouItem] = []
    proposals = conv_store.list_proposals(state="proposed", limit=100)
    for p in proposals:
        severity = "high" if p.risk in ("high", "critical") else "medium"
        url = f"/staff?thread={p.thread_id}&proposal={p.id}" if p.thread_id else f"/staff?proposal={p.id}"
        items.append(
            WaitingOnYouItem(
                id=f"approval:{p.id}",
                category="approval",
                title=f"Approval required: {p.action}",
                summary=f"Risk: {p.risk}. Parameters: {p.params}",
                source="proposals",
                severity=severity,
                action_url=url,
                thread_id=p.thread_id,
                created_at=p.created_at,
                metadata={"proposal_id": p.id, "action": p.action, "risk": p.risk, "params": p.params},
            )
        )
    return items


def _collect_questions(conv_store: ConversationStore, run_store: RunStore) -> list[WaitingOnYouItem]:
    items: list[WaitingOnYouItem] = []
    since = (datetime.now(UTC) - timedelta(hours=48)).isoformat().replace("+00:00", "Z")
    runs = run_store.list_runs(limit=100, since=since)
    for r in runs:
        if getattr(r, "failure_class", "") == "needs_input":
            thread_id = getattr(r, "thread_id", None)
            url = f"/staff?thread={thread_id}&run_id={r.id}" if thread_id else f"/staff?run_id={r.id}"
            items.append(
                WaitingOnYouItem(
                    id=f"question:run:{r.id}",
                    category="question",
                    title=f"Question from {r.role}",
                    summary=r.error or r.remediation or "Agent paused asking for input",
                    source="runs",
                    severity="high",
                    action_url=url,
                    thread_id=thread_id,
                    created_at=r.created_at,
                    metadata={"run_id": r.id, "role": r.role, "machine": r.machine, "provider": r.provider},
                )
            )
    return items


def _collect_escalations(run_store: RunStore, wi_store: WorkItemStore) -> list[WaitingOnYouItem]:
    items: list[WaitingOnYouItem] = []
    since = (datetime.now(UTC) - timedelta(hours=48)).isoformat().replace("+00:00", "Z")
    runs = run_store.list_runs(limit=100, since=since)

    escalation_classes = frozenset({"stalled", "unkillable", "workspace_error"})
    for r in runs:
        fc = getattr(r, "failure_class", "")
        if r.status in ("failed", "blocked") and fc in escalation_classes:
            thread_id = getattr(r, "thread_id", None)
            url = f"/staff?thread={thread_id}&run_id={r.id}" if thread_id else f"/staff?run_id={r.id}"
            items.append(
                WaitingOnYouItem(
                    id=f"escalation:run:{r.id}",
                    category="escalation",
                    title=f"Escalation: {r.role} run on {r.machine}",
                    summary=r.error or r.remediation or f"Run failed with {fc}",
                    source="runs",
                    severity="critical",
                    action_url=url,
                    thread_id=thread_id,
                    created_at=r.created_at,
                    metadata={"run_id": r.id, "role": r.role, "machine": r.machine, "failure_class": fc},
                )
            )

    try:
        escalated_wis = wi_store.list_work_items(state="escalated", limit=50)
        for wi in escalated_wis:
            url = f"/staff?thread={wi.thread_id}&work_item={wi.id}" if wi.thread_id else f"/staff?work_item={wi.id}"
            items.append(
                WaitingOnYouItem(
                    id=f"escalation:wi:{wi.id}",
                    category="escalation",
                    title=f"Escalation: {wi.title}",
                    summary=f"Work item marked as escalated for {wi.owner_role}",
                    source="work_items",
                    severity="critical",
                    action_url=url,
                    thread_id=wi.thread_id,
                    created_at=wi.created_at,
                    metadata={"work_item_id": wi.id, "role": wi.owner_role},
                )
            )
    except Exception:
        pass
    return items


async def _collect_project_decisions() -> list[WaitingOnYouItem]:
    import projects.service as ps

    items: list[WaitingOnYouItem] = []
    repos = ps.configured_repos()
    for repo in repos:
        overview = await ps.project_overview(repo)
        decisions = overview.get("decisions_needed") or []
        for idx, dec in enumerate(decisions):
            items.append(
                WaitingOnYouItem(
                    id=f"project_decision:{repo}:{idx}",
                    category="project_decision",
                    title=f"Project Decision needed: {repo}",
                    summary=dec,
                    source="projects",
                    severity="medium",
                    action_url=f"/projects/{repo}",
                    metadata={"repo": repo, "decision": dec},
                )
            )
    return items


def _collect_board_proposals(wi_store: WorkItemStore) -> list[WaitingOnYouItem]:
    items: list[WaitingOnYouItem] = []
    proposals = wi_store.list_work_items(owner_role="board_secretary", state="open", limit=50)
    for wi in proposals:
        if wi.title.startswith("[Board Proposal]") or (wi.links and "proposals" in wi.links):
            url = f"/staff?thread={wi.thread_id}&work_item={wi.id}" if wi.thread_id else f"/staff?work_item={wi.id}"
            items.append(
                WaitingOnYouItem(
                    id=f"board_proposal:{wi.id}",
                    category="board_proposal",
                    title=wi.title,
                    summary=f"Board proposal awaiting owner review for {wi.owner_role}",
                    source="board_proposals",
                    severity="high",
                    action_url=url,
                    thread_id=wi.thread_id,
                    created_at=wi.created_at,
                    metadata={"work_item_id": wi.id, "links": wi.links},
                )
            )
    return items


def _collect_auth_signins(run_store: RunStore) -> list[WaitingOnYouItem]:
    items: list[WaitingOnYouItem] = []
    since = (datetime.now(UTC) - timedelta(hours=48)).isoformat().replace("+00:00", "Z")
    recent = run_store.list_runs(limit=100, since=since)
    att_items = format_attention_items(recent)
    for att in att_items:
        if att.get("failure_class") == "auth_expired":
            machine = att.get("machine", "local")
            provider = att.get("provider", "unknown")
            items.append(
                WaitingOnYouItem(
                    id=f"auth_signin:{machine}:{provider}",
                    category="auth_signin",
                    title=f"Sign-in required: {provider} on {machine}",
                    summary=att.get("remediation") or f"Run login command on {machine}",
                    source="auth",
                    severity="critical",
                    action_url="/settings/credentials",
                    metadata={
                        "machine": machine,
                        "provider": provider,
                        "login_command": att.get("remediation", ""),
                    },
                )
            )
    return items


async def collect_waiting_on_you_inbox(caller_id: str = "") -> dict[str, Any]:
    """Collect unified inbox from all 6 sources with per-source graceful degradation."""
    conv_store = get_conversation_store()
    run_store = get_store()
    wi_store = get_work_item_store()

    sources: dict[str, dict[str, Any]] = {}
    items: list[WaitingOnYouItem] = []

    # 1. Approvals
    try:
        appr = _collect_approvals(conv_store)
        items.extend(appr)
        sources["approvals"] = {"status": "ok", "count": len(appr)}
    except Exception as exc:
        log.warning("staff inbox: approvals source failed: %s", exc)
        sources["approvals"] = {"status": "unavailable", "count": 0, "error": str(exc)}

    # 2. Questions
    try:
        qs = _collect_questions(conv_store, run_store)
        items.extend(qs)
        sources["questions"] = {"status": "ok", "count": len(qs)}
    except Exception as exc:
        log.warning("staff inbox: questions source failed: %s", exc)
        sources["questions"] = {"status": "unavailable", "count": 0, "error": str(exc)}

    # 3. Escalations
    try:
        escs = _collect_escalations(run_store, wi_store)
        items.extend(escs)
        sources["escalations"] = {"status": "ok", "count": len(escs)}
    except Exception as exc:
        log.warning("staff inbox: escalations source failed: %s", exc)
        sources["escalations"] = {"status": "unavailable", "count": 0, "error": str(exc)}

    # 4. Project Decisions
    try:
        pds = await _collect_project_decisions()
        items.extend(pds)
        sources["project_decisions"] = {"status": "ok", "count": len(pds)}
    except Exception as exc:
        log.warning("staff inbox: project decisions source failed: %s", exc)
        sources["project_decisions"] = {"status": "unavailable", "count": 0, "error": str(exc)}

    # 5. Board Proposals
    try:
        bps = _collect_board_proposals(wi_store)
        items.extend(bps)
        sources["board_proposals"] = {"status": "ok", "count": len(bps)}
    except Exception as exc:
        log.warning("staff inbox: board proposals source failed: %s", exc)
        sources["board_proposals"] = {"status": "unavailable", "count": 0, "error": str(exc)}

    # 6. Auth sign-ins
    try:
        auths = _collect_auth_signins(run_store)
        items.extend(auths)
        sources["auth_signins"] = {"status": "ok", "count": len(auths)}
    except Exception as exc:
        log.warning("staff inbox: auth signins source failed: %s", exc)
        sources["auth_signins"] = {"status": "unavailable", "count": 0, "error": str(exc)}

    # Unread threads (backwards compatibility with threads inbox callers)
    threads_items: list[dict[str, Any]] = []
    try:
        open_threads = conv_store.list_threads(status="open", limit=200)
        for th in open_threads:
            unread = th.unread_counters.get(caller_id, 0) if caller_id else sum(th.unread_counters.values())
            proposals = conv_store.list_proposals(thread_id=th.id, state="proposed", limit=10)
            if unread > 0 or proposals:
                t_dict = th.to_dict()
                t_dict["pending_proposals_count"] = len(proposals)
                t_dict["caller_unread_count"] = unread
                threads_items.append(t_dict)
                # Also include thread in items with its exact thread ID for back-compat matching
                items.append(
                    WaitingOnYouItem(
                        id=th.id,
                        category="thread",
                        title=th.title,
                        summary=f"Unread thread ({unread} new message{'s' if unread != 1 else ''})",
                        source="threads",
                        severity="medium",
                        action_url=f"/staff?thread={th.id}",
                        thread_id=th.id,
                        created_at=th.last_message_at or th.created_at,
                        metadata=t_dict,
                    )
                )
    except Exception as exc:
        log.warning("staff inbox: threads collector failed: %s", exc)

    counts = {
        "approvals": sources.get("approvals", {}).get("count", 0),
        "questions": sources.get("questions", {}).get("count", 0),
        "escalations": sources.get("escalations", {}).get("count", 0),
        "project_decisions": sources.get("project_decisions", {}).get("count", 0),
        "board_proposals": sources.get("board_proposals", {}).get("count", 0),
        "auth_signins": sources.get("auth_signins", {}).get("count", 0),
        "threads": len(threads_items),
    }

    item_dicts = [it.to_dict() for it in items]

    return {
        "count": len(items),
        "counts": counts,
        "items": item_dicts,
        "inbox": item_dicts,  # back-compat alias
        "threads": threads_items,
        "sources": sources,
    }


def generate_barb_briefing(period: str = "morning", inbox_data: dict[str, Any] | None = None) -> str:
    """Generate structured markdown briefing from Barb for the owner."""
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    period_title = (
        "Morning Briefing"
        if period == "morning"
        else "Evening Briefing"
        if period == "evening"
        else "On-Demand Briefing"
    )

    data = inbox_data or {}
    items = data.get("items") or []
    counts = data.get("counts") or {}
    total_waiting = len(items)

    lines: list[str] = [
        f"### 📋 Barb's {period_title} — {now_str}",
        "",
        f"**Waiting on You ({total_waiting} items)**",
    ]

    if not items:
        lines.append("- ✨ No pending approvals, questions, or escalations. All clear!")
    else:
        for it in items[:10]:
            cat_icon = {
                "approval": "✍️",
                "question": "❓",
                "escalation": "🚨",
                "project_decision": "🎯",
                "board_proposal": "🏛️",
                "auth_signin": "🔑",
                "thread": "💬",
            }.get(it.get("category", ""), "📌")
            lines.append(f"- {cat_icon} **{it.get('title')}**: {it.get('summary')}")

    lines.extend(
        [
            "",
            "**Category Summary**",
            (
                f"- Approvals: {counts.get('approvals', 0)} | "
                f"Questions: {counts.get('questions', 0)} | "
                f"Escalations: {counts.get('escalations', 0)}"
            ),
            (
                f"- Decisions: {counts.get('project_decisions', 0)} | "
                f"Proposals: {counts.get('board_proposals', 0)} | "
                f"Auth: {counts.get('auth_signins', 0)}"
            ),
            "",
            "> *Use `/brief` to refresh this briefing, or open the Waiting on You panel in Staff Console.*",
        ]
    )

    return "\n".join(lines)


def post_barb_briefing(
    conv_store: ConversationStore,
    period: str = "morning",
    caller: str = "scheduler",
    inbox_data: dict[str, Any] | None = None,
) -> MessageRecord:
    """Find or create Barb's conversation thread and post a structured briefing message."""
    threads = conv_store.list_threads(kind="direct", limit=50)
    barb_thread = next((t for t in threads if "barb" in t.participants), None)

    if not barb_thread:
        barb_thread = conv_store.create_thread(
            title="Barb (Secretary)",
            kind="direct",
            participants=["barb", "dieter"],
            created_by=caller,
        )

    briefing_text = generate_barb_briefing(period=period, inbox_data=inbox_data)

    msg = conv_store.add_message(
        thread_id=barb_thread.id,
        author_kind="role",
        author="barb",
        body_md=briefing_text,
        kind="text",
        meta={"kind": "briefing", "period": period, "generated_by": caller},
        delivery="complete",
    )
    return msg
