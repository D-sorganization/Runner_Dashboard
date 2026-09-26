"""Link staff runs to conversation threads, post run cards, and handle needs_input (SC-B7, Issue #1314).

Ensures all background work dispatched from or associated with a conversation thread
reports progress directly back into the thread as structured run_card messages,
and that unattended agents stopping with questions (needs_input) can receive answers.
"""

from __future__ import annotations

import logging
from typing import Any

from staff.conversation_models import MessageRecord
from staff.conversations import get_conversation_store
from staff.plan import RunRequest
from staff.store import RunRecord
from staff.thread_bus import get_thread_bus

log = logging.getLogger("dashboard.staff.run_link")

# Run status -> RunCard status (``RunStatus`` in StaffConsole/cards/cardTypes.ts).
_CARD_STATUS = {
    "queued": "queued",
    "preparing": "running",
    "running": "running",
    "needs_input": "needs_input",
    "succeeded": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}


def result_summary(result_line: str) -> str | None:
    """The text of a run's ``STAFF_RESULT:`` line (first line only); ``None`` when it said nothing."""
    _, sep, rest = (result_line or "").partition("STAFF_RESULT:")
    text = rest.strip().splitlines()[0].strip() if sep and rest.strip() else ""
    return text or None


def run_card_id(run_id: str) -> str:
    """The id of the one run card a run has in its thread."""
    return f"msg-card-{run_id}"


def card_run(run: RunRecord, status: str, question: str | None = None, summary: str | None = None) -> dict[str, Any]:
    """The ``meta.run`` the Console renders (``RunCardData``).

    Post: ``status`` is a ``RunStatus`` (unknown statuses read ``running``); ``question`` is
    set only while the run needs input.
    """
    return {
        "id": run.id,
        "status": _CARD_STATUS.get(status, "running"),
        "role": run.role,
        "node": run.machine,
        "provider": run.provider,
        "repo": run.repo,
        "thread_id": getattr(run, "thread_id", "") or "",
        "question": question if status == "needs_input" else None,
        "summary": summary or getattr(run, "outcome", "") or None,
        "error": getattr(run, "error", "") or None,
        "failure_class": getattr(run, "failure_class", "") or None,
    }


def format_run_card_body(
    run: RunRecord,
    status: str,
    question: str | None = None,
    summary: str | None = None,
) -> str:
    """Generate user-facing Markdown for a run_card message."""
    lines = [
        f"### Run `{run.id}` ({run.role})",
        f"- **Status**: `{status}`",
        f"- **Machine**: `{run.machine}`",
    ]
    if run.repo:
        ref_str = f" ({run.target_ref})" if run.target_ref else ""
        lines.append(f"- **Target**: `{run.repo}`{ref_str}")
    if run.branch:
        lines.append(f"- **Branch**: `{run.branch}`")

    if status == "needs_input" and question:
        lines.append(f"\n> ❓ **Question from agent**:\n> {question}\n")
        lines.append("*Reply in this thread to provide your answer.*")
    elif status == "succeeded":
        res = summary or run.outcome or "Completed successfully."
        lines.append(f"\n✅ **Result**: {res}")
    elif status == "failed":
        err_msg = run.error or "Run failed."
        cls_msg = f" [{run.failure_class}]" if run.failure_class else ""
        lines.append(f"\n❌ **Error**{cls_msg}: {err_msg}")
        if run.remediation:
            lines.append(f"\n*Remediation*: {run.remediation}")
    elif status == "cancelled":
        lines.append("\n⚠️ **Cancelled by operator.**")

    return "\n".join(lines)


def post_run_card(
    run: RunRecord,
    status: str,
    text: str = "",
    question: str | None = None,
    summary: str | None = None,
    store: Any = None,
    bus: Any = None,
) -> MessageRecord | None:
    """Post the run's card in its thread, or rewrite it on a later status (#1547).

    Post: a run has one ``run_card`` message (:func:`run_card_id`), published on the
    thread bus as a ``message`` event so an open Console updates it in place.
    """
    thread_id = getattr(run, "thread_id", "") or ""
    if not thread_id:
        return None

    conv_store = store or get_conversation_store()
    event_bus = bus or get_thread_bus()

    body_md = text or format_run_card_body(run, status, question=question, summary=summary)
    meta = {
        "run_id": run.id,
        "role": run.role,
        "provider": run.provider,
        "machine": run.machine,
        "status": status,
        "repo": run.repo,
        "target_ref": run.target_ref,
        "failure_class": getattr(run, "failure_class", ""),
        "error": getattr(run, "error", ""),
        "question": question,
        "summary": summary or getattr(run, "outcome", ""),
        "work_item_id": getattr(run, "work_item_id", ""),
        "run": card_run(run, status, question=question, summary=summary),
    }

    msg_id = run_card_id(run.id)
    try:
        existing = conv_store.get_message(msg_id)
        if existing and existing.meta:
            existing_status = existing.meta.get("status")
            if existing_status in (
                "succeeded",
                "failed",
                "cancelled",
            ) and status not in (
                "succeeded",
                "failed",
                "cancelled",
            ):
                return existing
        saved = conv_store.update_message(msg_id, body_md=body_md, meta=meta) or conv_store.add_message(
            thread_id=thread_id,
            author_kind="role",
            author=run.role,
            kind="run_card",
            body_md=body_md,
            meta=meta,
            run_id=run.id,
            delivery="complete",
            message_id=msg_id,
        )
        event_bus.publish_message_sync(thread_id, saved.to_dict())
        return saved
    except Exception as exc:  # noqa: BLE001
        log.warning("staff.run_link: failed to post run card for %s: %s", run.id, exc)
        return None


def update_linked_work_item(run: RunRecord, status: str, summary: str | None = None) -> None:
    """Update linked work item state and links from run status change."""
    work_item_id = getattr(run, "work_item_id", "") or ""
    if not work_item_id:
        return
    try:
        from staff.work_items import get_work_item_store  # noqa: PLC0415

        wi_store = get_work_item_store()
        wi = wi_store.get_work_item(work_item_id)
        if not wi:
            return
        wi_store.add_link(work_item_id, "runs", run.id)
        if status in ("preparing", "running") and wi.state in (
            "open",
            "waiting_on_user",
        ):
            wi_store.transition_state(
                work_item_id,
                "in_progress",
                actor=run.role,
                reason=f"Run {run.id} started",
            )
        elif status == "needs_input" and wi.state in ("open", "in_progress"):
            wi_store.transition_state(
                work_item_id,
                "waiting_on_user",
                actor=run.role,
                reason=f"Run {run.id} needs input",
            )
        elif status == "succeeded" and wi.state in (
            "open",
            "in_progress",
            "waiting_on_ci",
        ):
            outcome = summary or getattr(run, "outcome", "") or ""
            if "PR #" in outcome or "pull/" in outcome:
                wi_store.transition_state(
                    work_item_id,
                    "waiting_on_ci",
                    actor=run.role,
                    reason=f"Run {run.id} opened PR",
                )
            else:
                wi_store.transition_state(
                    work_item_id,
                    "done",
                    actor=run.role,
                    reason=f"Run {run.id} completed successfully",
                )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "staff.run_link: failed to update work item %s for run %s: %s",
            work_item_id,
            run.id,
            exc,
        )


def relay_run_card_to_origin(
    run: RunRecord,
    status: str,
    question: str | None = None,
    summary: str | None = None,
    relay_fn: Any = None,
) -> bool:
    """Relay a forwarded run's card event back to the thread's home node (issue #1488)."""
    origin_node = getattr(run, "origin_node", "") or ""
    thread_id = getattr(run, "thread_id", "") or ""
    machine = getattr(run, "machine", "") or ""
    if not origin_node or not thread_id or origin_node == machine:
        return False

    from staff import fleet as staff_fleet  # noqa: PLC0415

    nodes = staff_fleet.peer_nodes()
    origin_url = nodes.get(origin_node)
    if not origin_url:
        log.warning(
            "staff.run_link: cannot relay run card for %s: unknown peer '%s'",
            run.id,
            origin_node,
        )
        return False

    payload = {
        "run_id": run.id,
        "role": run.role,
        "status": status,
        "node": machine,
        "provider": run.provider,
        "repo": run.repo,
        "target_ref": run.target_ref,
        "branch": run.branch,
        "question": question,
        "summary": summary,
        "error": getattr(run, "error", "") or None,
        "failure_class": getattr(run, "failure_class", "") or None,
    }
    headers = staff_fleet.fleet_headers()
    target_url = f"{origin_url}/api/v1/staff/threads/{thread_id}/relay-card"
    try:
        if relay_fn is not None:
            resp = relay_fn(target_url, payload, headers)
            status_code = getattr(resp, "status_code", 200)
        else:
            import httpx  # noqa: PLC0415

            with httpx.Client(timeout=staff_fleet.PEER_TIMEOUT_SECONDS) as client:
                resp = client.post(target_url, json=payload, headers=headers)
                status_code = resp.status_code
        if status_code >= 400:
            log.warning(
                "staff.run_link: peer %s returned HTTP %s for run card relay %s",
                origin_node,
                status_code,
                run.id,
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "staff.run_link: failed to relay run card for %s to %s (%s): %s",
            run.id,
            origin_node,
            target_url,
            exc,
        )
        return False


def handle_run_status_change(
    run: RunRecord,
    status: str,
    question: str | None = None,
    summary: str | None = None,
    store: Any = None,
    bus: Any = None,
    relay_fn: Any = None,
) -> MessageRecord | None:
    """Handler called on run transition (queued, running, needs_input, succeeded, failed)."""
    update_linked_work_item(run, status, summary=summary)
    card = post_run_card(
        run,
        status=status,
        question=question,
        summary=summary,
        store=store,
        bus=bus,
    )
    relay_run_card_to_origin(run, status, question=question, summary=summary, relay_fn=relay_fn)
    return card


def answer_needs_input(
    thread_id: str,
    run_id: str,
    answer: str,
    caller_id: str,
    conv_store: Any = None,
    run_store: Any = None,
    runner: Any = None,
) -> RunRecord | None:
    """Provide an answer to a needs_input run and initiate a continuation run."""
    c_store = conv_store or get_conversation_store()
    from staff.runner import get_runner  # noqa: PLC0415
    from staff.store import get_store  # noqa: PLC0415

    r_store = run_store or get_store()
    r_runner = runner or get_runner()

    prior_run = r_store.get_run(run_id)
    if prior_run is None:
        log.warning("staff.run_link: answer_needs_input for unknown run %s", run_id)
        return None

    continuation_prompt = (
        f"Continuation of run {run_id}.\n"
        f"Prior prompt:\n{prior_run.prompt}\n\n"
        f"User answer to question:\n{answer.strip()}"
    )

    req = RunRequest(
        role=prior_run.role,
        provider=prior_run.provider,
        model=prior_run.model,
        repo=prior_run.repo,
        prompt=continuation_prompt,
        machine=prior_run.machine,
        requested_by=caller_id,
        thread_id=thread_id,
        work_item_id=getattr(prior_run, "work_item_id", ""),
    )

    rec = r_runner.submit(req)
    handle_run_status_change(rec, "queued", store=c_store, bus=get_thread_bus())
    _mark_answered(c_store, get_thread_bus(), run_id, answered_by=caller_id, continued_by=rec.id)
    return rec


def _mark_answered(conv_store: Any, bus: Any, run_id: str, *, answered_by: str, continued_by: str) -> None:
    """Record on the question's card who answered and which run continues it."""
    card = conv_store.get_message(run_card_id(run_id))
    if card is None:
        return
    run = {
        **(card.meta.get("run") or {}),
        "answered_by": answered_by,
        "continued_by": continued_by,
    }
    saved = conv_store.update_message(card.id, meta={**card.meta, "run": run})
    if saved is not None:
        bus.publish_message_sync(saved.thread_id, saved.to_dict())


def apply_relayed_run_card(store: Any, bus: Any, thread_id: str, req: Any) -> Any:
    """Apply a relayed run-card update received from an executing peer node."""
    run_stub = RunRecord(
        id=req.run_id,
        role=req.role,
        provider=req.provider,
        model=None,
        machine=req.node,
        repo=req.repo,
        target_kind="prompt",
        target_ref=req.target_ref,
        prompt="",
        branch=req.branch,
        thread_id=thread_id,
        error=req.error or "",
        failure_class=req.failure_class or "",
    )
    return post_run_card(
        run_stub,
        status=req.status,
        text=req.body_md or "",
        question=req.question,
        summary=req.summary,
        store=store,
        bus=bus,
    )
