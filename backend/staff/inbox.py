"""Waiting on you Inbox and Barb Briefings Engine (SC-C5, Issue #1328).

Aggregates actionable items requiring operator/owner attention:
- Pending action proposals awaiting approval (SC-B6 #1313)
- Needs-input questions from stalled/clarifying agent runs (SC-B7 #1314)
- Escalated work items from Barb's follow-up engine (SC-C3 #1316)
- Decisions needed recorded in repository STATUS.md charters (Issue #1199)
- Board proposals awaiting the owner
- Provider authentication sign-ins required

Fault-tolerant: Any failing source reports 'unavailable' with error detail
while surviving sources continue to aggregate cleanly.
"""

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from push import send_push
from staff.audit import record_audit
from staff.conversations import (
    ConversationStore,
    get_conversation_store,
)
from staff.store import RunStore
from staff.store import get_store as get_run_store
from staff.work_items import WorkItemStore, get_work_item_store
from time_utils import utc_now_iso

log = logging.getLogger("dashboard.staff.inbox")

InboxSource = Literal[
    "approval",
    "needs_input",
    "escalation",
    "project_decision",
    "board_proposal",
    "auth_sign_in",
]


@dataclass(frozen=True)
class InboxItem:
    """A single item requiring owner/operator attention."""

    id: str
    source: InboxSource
    title: str
    summary: str
    severity: Literal["low", "medium", "high", "critical"]
    created_at: str
    link: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceStatus:
    """Status of an individual inbox aggregation source."""

    status: Literal["ok", "unavailable"]
    count: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InboxAggregate:
    """Full aggregated inbox payload."""

    items: list[InboxItem]
    counts: dict[str, int]
    sources: dict[str, SourceStatus]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        item_dicts = [item.to_dict() for item in self.items]
        return {
            "items": item_dicts,
            "inbox": item_dicts,
            "count": self.counts["total"],
            "counts": self.counts,
            "sources": {k: v.to_dict() for k, v in self.sources.items()},
            "generated_at": self.generated_at,
        }


def _collect_approvals(c_store: ConversationStore) -> list[InboxItem]:
    """Collect pending proposals awaiting approval."""
    proposals = c_store.list_proposals(state="proposed", limit=100)
    items: list[InboxItem] = []
    for prop in proposals:
        sev: Literal["low", "medium", "high", "critical"] = "medium"
        if prop.risk == "high":
            sev = "high"
        elif prop.risk == "low":
            sev = "low"
        summary = (
            prop.params.get("rationale") if isinstance(prop.params, dict) else ""
        ) or f"Action {prop.action} proposed for thread {prop.thread_id}"
        items.append(
            InboxItem(
                id=f"approval_{prop.id}",
                source="approval",
                title=f"Approval needed: {prop.action}",
                summary=summary,
                severity=sev,
                created_at=prop.created_at,
                link=f"/staff?thread={prop.thread_id}",
                metadata={
                    "proposal_id": prop.id,
                    "thread_id": prop.thread_id,
                    "action": prop.action,
                    "risk": prop.risk,
                    "params": prop.params,
                },
            )
        )
    return items


def _collect_needs_input(
    r_store: RunStore,
    w_store: WorkItemStore,
    c_store: ConversationStore | None = None,
    caller_id: str | None = None,
) -> list[InboxItem]:
    """Collect agent runs, work items, and unread threads stopped waiting for user input."""
    items: list[InboxItem] = []
    seen_runs: set[str] = set()

    # 1. Runs with status="needs_input"
    runs = r_store.list_runs(status="needs_input", limit=50)
    for run in runs:
        seen_runs.add(run.id)
        q = getattr(run, "outcome", "") or getattr(run, "last_line", "") or ""
        items.append(
            InboxItem(
                id=f"needs_input_{run.id}",
                source="needs_input",
                title=f"Question from {run.role}",
                summary=q or run.prompt[:200],
                severity="medium",
                created_at=run.created_at,
                link=f"/staff?run={run.id}",
                metadata={"run_id": run.id, "role": run.role, "repo": run.repo, "question": q},
            )
        )

    # 2. Work items with state="waiting_on_user"
    wis = w_store.list_work_items(state="waiting_on_user", limit=50)
    for wi in wis:
        if wi.run_id and wi.run_id in seen_runs:
            continue
        link = f"/staff?thread={wi.thread_id}" if wi.thread_id else f"/staff?work_item={wi.id}"
        items.append(
            InboxItem(
                id=f"waiting_wi_{wi.id}",
                source="needs_input",
                title=f"Input needed: {wi.title}",
                summary=f"Work item {wi.id} waiting on user input",
                severity="medium",
                created_at=wi.updated_at,
                link=link,
                metadata={"work_item_id": wi.id, "owner_role": wi.owner_role},
            )
        )

    # 3. Unread threads waiting on user attention
    if c_store is not None:
        try:
            open_threads = c_store.list_threads(status="open", limit=50)
            for th in open_threads:
                has_unread = (
                    th.unread_counters.get(caller_id, 0) > 0
                    if caller_id
                    else any(cnt > 0 for cnt in th.unread_counters.values())
                )
                if has_unread:
                    items.append(
                        InboxItem(
                            id=th.id,
                            source="needs_input",
                            title=f"Unread in {th.title}",
                            summary=f"Unread message(s) in thread {th.title}",
                            severity="medium",
                            created_at=th.updated_at or th.created_at,
                            link=f"/staff?thread={th.id}",
                            metadata={
                                "thread_id": th.id,
                                "unread_count": th.unread_counters.get(caller_id or "", 0),
                            },
                        )
                    )
        except Exception as exc:
            log.warning("Inbox failed collecting unread threads: %s", exc)

    return items


def _collect_escalations(w_store: WorkItemStore) -> list[InboxItem]:
    """Collect escalated work items requiring immediate owner attention."""
    items: list[InboxItem] = []
    wis = w_store.list_work_items(state="escalated", limit=50)
    for wi in wis:
        link = f"/staff?thread={wi.thread_id}" if wi.thread_id else f"/staff?work_item={wi.id}"
        items.append(
            InboxItem(
                id=f"escalation_{wi.id}",
                source="escalation",
                title=f"Escalated: {wi.title}",
                summary=f"Work item {wi.id} escalated to Barb / Owner",
                severity="critical",
                created_at=wi.updated_at,
                link=link,
                metadata={"work_item_id": wi.id, "owner_role": wi.owner_role},
            )
        )
    return items


async def _collect_project_decisions() -> list[InboxItem]:
    """Collect decisions needed from repository STATUS.md project charters."""
    items: list[InboxItem] = []
    from projects import service as proj_service

    repos = proj_service.configured_repos()
    for repo in repos:
        overview = await proj_service.project_overview(repo)
        decisions = overview.get("decisions_needed", [])
        for idx, dec in enumerate(decisions):
            items.append(
                InboxItem(
                    id=f"project_dec_{repo}_{idx}",
                    source="project_decision",
                    title=f"Decision needed in {repo}",
                    summary=str(dec),
                    severity="medium",
                    created_at=overview.get("generated_at") or utc_now_iso(),
                    link=f"/projects/{repo}",
                    metadata={"repo": repo, "decision": dec},
                )
            )
    return items


def _collect_auth_sign_ins() -> list[InboxItem]:
    """Collect providers requiring authentication sign-in."""
    items: list[InboxItem] = []
    try:
        from agent_remediation import PROVIDER_REGISTRY
        from agent_remediation.provider_probe import probe_provider_availability

        probes = probe_provider_availability()
        for entry in PROVIDER_REGISTRY:
            if not entry.enabled:
                continue
            avail = probes.get(entry.dashboard_id)
            if avail and avail.get("installed") and not avail.get("authenticated"):
                detail = avail.get("detail", "")
                items.append(
                    InboxItem(
                        id=f"auth_{entry.dashboard_id}",
                        source="auth_sign_in",
                        title=f"Sign-in required: {entry.label}",
                        summary=f"Provider {entry.label} is installed but requires authentication ({detail}).",
                        severity="high",
                        created_at=utc_now_iso(),
                        link="/settings",
                        metadata={"provider_id": entry.dashboard_id, "detail": detail},
                    )
                )
    except Exception as exc:
        log.warning("Failed probing provider auth sign-ins: %s", exc)
        raise
    return items


async def collect_inbox(
    c_store: ConversationStore | None = None,
    r_store: RunStore | None = None,
    w_store: WorkItemStore | None = None,
    caller_id: str | None = None,
) -> InboxAggregate:
    """Aggregate all waiting-on-you items with per-source fault isolation."""
    c_store = c_store or get_conversation_store()
    r_store = r_store or get_run_store()
    w_store = w_store or get_work_item_store()

    all_items: list[InboxItem] = []
    sources: dict[str, SourceStatus] = {}
    counts: dict[str, int] = {
        "total": 0,
        "approvals": 0,
        "needs_input": 0,
        "escalations": 0,
        "project_decisions": 0,
        "board_proposals": 0,
        "auth_sign_ins": 0,
    }

    # 1. Approvals
    try:
        appr_items = _collect_approvals(c_store)
        all_items.extend(appr_items)
        counts["approvals"] = len(appr_items)
        sources["approvals"] = SourceStatus(status="ok", count=len(appr_items))
    except Exception as exc:
        log.exception("Inbox failed collecting approvals: %s", exc)
        sources["approvals"] = SourceStatus(status="unavailable", count=0, error=str(exc))

    # 2. Needs input
    try:
        ni_items = _collect_needs_input(r_store, w_store, c_store, caller_id)
        all_items.extend(ni_items)
        counts["needs_input"] = len(ni_items)
        sources["needs_input"] = SourceStatus(status="ok", count=len(ni_items))
    except Exception as exc:
        log.exception("Inbox failed collecting needs_input: %s", exc)
        sources["needs_input"] = SourceStatus(status="unavailable", count=0, error=str(exc))

    # 3. Escalations
    try:
        esc_items = _collect_escalations(w_store)
        all_items.extend(esc_items)
        counts["escalations"] = len(esc_items)
        sources["escalations"] = SourceStatus(status="ok", count=len(esc_items))
    except Exception as exc:
        log.exception("Inbox failed collecting escalations: %s", exc)
        sources["escalations"] = SourceStatus(status="unavailable", count=0, error=str(exc))

    # 4. Project Decisions
    try:
        pd_items = await _collect_project_decisions()
        all_items.extend(pd_items)
        counts["project_decisions"] = len(pd_items)
        sources["project_decisions"] = SourceStatus(status="ok", count=len(pd_items))
    except Exception as exc:
        log.warning("Inbox failed collecting project_decisions: %s", exc)
        sources["project_decisions"] = SourceStatus(status="unavailable", count=0, error=str(exc))

    # 5. Board Proposals (graceful stub until board proposal backend lands)
    try:
        bp_items: list[InboxItem] = []
        all_items.extend(bp_items)
        counts["board_proposals"] = len(bp_items)
        sources["board_proposals"] = SourceStatus(status="ok", count=len(bp_items))
    except Exception as exc:
        log.warning("Inbox failed collecting board_proposals: %s", exc)
        sources["board_proposals"] = SourceStatus(status="unavailable", count=0, error=str(exc))

    # 6. Auth sign-ins
    try:
        auth_items = _collect_auth_sign_ins()
        all_items.extend(auth_items)
        counts["auth_sign_ins"] = len(auth_items)
        sources["auth_sign_ins"] = SourceStatus(status="ok", count=len(auth_items))
    except Exception as exc:
        log.warning("Inbox failed collecting auth_sign_ins: %s", exc)
        sources["auth_sign_ins"] = SourceStatus(status="unavailable", count=0, error=str(exc))

    counts["total"] = len(all_items)

    # Sort descending by severity (critical, high, medium, low) then recency
    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_items.sort(key=lambda it: (sev_rank.get(it.severity, 4), it.created_at), reverse=False)

    return InboxAggregate(
        items=all_items,
        counts=counts,
        sources=sources,
        generated_at=utc_now_iso(),
    )


def generate_briefing(
    inbox: InboxAggregate,
    r_store: RunStore | None = None,
    kind: str = "morning",
) -> str:
    """Generate structured Markdown briefing for Barb's thread."""
    r_store = r_store or get_run_store()
    now_str = datetime.now(UTC).strftime("%A, %B %d, %Y %H:%M UTC")
    kind_title = "Morning" if kind.lower() == "morning" else ("Evening" if kind.lower() == "evening" else "On-Demand")

    # In-flight runs
    active_runs = r_store.list_runs(status="running", limit=10)
    queued_runs = r_store.list_runs(status="queued", limit=10)

    lines: list[str] = [
        f"# Barb's {kind_title} Briefing",
        f"*{now_str}*",
        "",
        f"## 🚨 Waiting on You ({inbox.counts['total']})",
    ]

    if not inbox.items:
        lines.append("All clear! No pending approvals, escalations, or questions awaiting you.")
    else:
        for item in inbox.items[:15]:
            sev_badge = f"**[{item.severity.upper()}]**" if item.severity in ("critical", "high") else ""
            lines.append(f"- {sev_badge} [{item.title}]({item.link}): {item.summary}")
        if len(inbox.items) > 15:
            lines.append(f"- *...and {len(inbox.items) - 15} more items in your inbox.*")

    active_desc = ", ".join(f"`{r.role}` ({r.repo})" for r in active_runs) if active_runs else "None"
    queued_desc = ", ".join(f"`{r.role}` ({r.repo})" for r in queued_runs) if queued_runs else "None"
    lines.extend(
        [
            "",
            "## 🚀 In-Flight & Queued Work",
            f"- **Running ({len(active_runs)})**: {active_desc}",
            f"- **Queued ({len(queued_runs)})**: {queued_desc}",
        ]
    )

    unavailable = [k for k, v in inbox.sources.items() if v.status == "unavailable"]
    if unavailable:
        lines.extend(
            [
                "",
                "## ⚠️ Degraded Sources",
                f"The following inbox sources are temporarily unavailable: {', '.join(unavailable)}.",
            ]
        )

    return "\n".join(lines)


async def post_briefing_to_barb(
    kind: str = "morning",
    c_store: ConversationStore | None = None,
    r_store: RunStore | None = None,
    w_store: WorkItemStore | None = None,
) -> dict[str, Any]:
    """Compile briefing, post to Barb's thread, and record audit log."""
    c_store = c_store or get_conversation_store()
    r_store = r_store or get_run_store()
    w_store = w_store or get_work_item_store()

    inbox = await collect_inbox(c_store=c_store, r_store=r_store, w_store=w_store)
    body_md = generate_briefing(inbox, r_store=r_store, kind=kind)

    # Find or create Barb's thread
    open_threads = c_store.list_threads(status="open", limit=50)
    barb_thread = next((th for th in open_threads if "barb" in [p.lower() for p in th.participants]), None)
    if not barb_thread:
        barb_thread = c_store.create_thread(title="Barb", kind="direct", participants=["barb", "user"])

    msg = c_store.add_message(
        thread_id=barb_thread.id,
        author_kind="role",
        author="barb",
        body_md=body_md,
        kind="text",
        meta={"briefing_kind": kind, "waiting_count": inbox.counts["total"]},
    )

    record_audit(
        action="staff_briefing_posted",
        principal="barb",
        target=f"thread:{barb_thread.id}",
        detail={"briefing_id": msg.id, "kind": kind, "waiting_count": inbox.counts["total"]},
    )

    return {
        "ok": True,
        "briefing_id": msg.id,
        "thread_id": barb_thread.id,
        "kind": kind,
        "waiting_count": inbox.counts["total"],
        "body_md": body_md,
    }


async def send_escalation_push(item: InboxItem) -> int:
    """Send web push notification for critical escalations with deep links."""
    payload = {
        "title": f"🚨 {item.title}",
        "body": item.summary[:200],
        "deep_link": item.link,
        "topic": "staff.escalation",
        "severity": item.severity,
        "item_id": item.id,
        "timestamp": item.created_at,
    }
    try:
        res = await send_push(topic="staff.escalation", payload=payload)
        return int(res.get("sent", 0)) if isinstance(res, dict) else int(res)
    except Exception as exc:
        log.warning("Failed sending escalation push for %s: %s", item.id, exc)
        return 0
