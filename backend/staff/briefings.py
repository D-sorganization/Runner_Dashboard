"""Barb Briefings Engine (SC-C5, Issue #1328).

Generates morning, evening, and on-demand Markdown briefings from
the aggregated owner inbox and active run store, and posts them to
Barb's direct thread with audit logging.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from staff.audit import record_audit
from staff.conversations import (
    ConversationStore,
    get_conversation_store,
)
from staff.store import RunStore
from staff.store import get_store as get_run_store
from staff.work_items import WorkItemStore, get_work_item_store

if TYPE_CHECKING:
    from staff.inbox import InboxAggregate

log = logging.getLogger("dashboard.staff.briefings")


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
    from staff.inbox import collect_inbox

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
