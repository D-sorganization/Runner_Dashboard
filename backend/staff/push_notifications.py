"""Push notifications integration for staff escalations (SC-C5, Issue #1328).

Emits Web Push notifications via existing /api/push (push.send_push)
exclusively for escalations, with deep links directly to the thread.
"""

from __future__ import annotations

import logging
from typing import Any

import push

log = logging.getLogger("dashboard.staff.push_notifications")


async def notify_escalation_push(item: dict[str, Any]) -> bool:
    """Send a Web Push notification for an escalation inbox item.

    Only items with category='escalation' are sent. Returns True if a push
    was dispatched, False if the item was ignored (not an escalation) or failed.
    """
    category = item.get("category")
    if category != "escalation":
        return False

    title = item.get("title") or "Staff Escalation"
    summary = item.get("summary") or "An operation requires owner attention."
    action_url = item.get("action_url") or (
        f"/staff?thread={item.get('thread_id')}" if item.get("thread_id") else "/staff"
    )

    payload = {
        "topic": "staff.escalation",
        "title": title,
        "body": summary[:250],
        "deep_link": action_url,
    }

    try:
        await push.send_push("staff.escalation", payload)
        log.info("staff: escalation push sent for item %s", item.get("id"))
        return True
    except Exception as exc:  # noqa: BLE001 — push failure must not crash staff flow
        log.warning("staff: failed to send escalation push for item %s: %s", item.get("id"), exc)
        return False
