"""Link staff runs to conversation threads, post run cards, and handle needs_input (SC-B7, Issue #1314).

Ensures all background work dispatched from or associated with a conversation thread
reports progress directly back into the thread as structured run_card messages,
and that unattended agents stopping with questions (needs_input) can receive answers.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from staff import claim_guard
from staff.barb_fence import FencedAuthorityError
from staff.barb_runtime import enqueue_run_card, get_authority_config, relay_payload
from staff.barb_versions import ApplyOutcome, ApplyResult
from staff.completion import work_item_state_for_success
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


def post_run_card_checked(
    run: RunRecord,
    status: str,
    text: str = "",
    question: str | None = None,
    summary: str | None = None,
    store: Any = None,
    bus: Any = None,
    version: int | None = None,
    epoch: int | None = None,
    authority_epoch: int = 0,
    authority_node: str = "",
) -> tuple[MessageRecord | None, ApplyResult | None]:
    """Version-gated run-card upsert that lets :class:`StaleEpochError` and storage errors propagate.

    Post: ``(card, result)``; ``(None, None)`` for a run without a thread. A rejected (DUPLICATE/STALE)
    update is not published. An APPLIED card is published on the thread bus.
    """
    thread_id = getattr(run, "thread_id", "") or ""
    if not thread_id:
        return None, None

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
    # One atomic, version-gated upsert: a late/duplicate update can never regress a terminal
    # card (#1800); a rejected update is not published.
    saved, result = conv_store.apply_run_card_update(
        thread_id=thread_id,
        run_id=run.id,
        message_id=run_card_id(run.id),
        status=status,
        version=version,
        epoch=epoch,
        authority_epoch=authority_epoch,
        authority_node=authority_node,
        body_md=body_md,
        meta=meta,
        author=run.role,
    )
    if result.outcome is ApplyOutcome.APPLIED and saved is not None:
        event_bus.publish_message_sync(thread_id, saved.to_dict())
    return saved, result


def post_run_card(
    run: RunRecord,
    status: str,
    text: str = "",
    question: str | None = None,
    summary: str | None = None,
    store: Any = None,
    bus: Any = None,
    version: int | None = None,
    epoch: int | None = None,
    authority_epoch: int = 0,
    authority_node: str = "",
) -> MessageRecord | None:
    """Post the run's card in its thread, or rewrite it on a later status (#1547).

    Post: a run has one ``run_card`` message (:func:`run_card_id`), published on the
    thread bus as a ``message`` event so an open Console updates it in place. Failures are logged.
    """
    try:
        return post_run_card_checked(
            run, status, text, question, summary, store, bus, version, epoch, authority_epoch, authority_node
        )[0]
    except FencedAuthorityError as exc:
        # A fenced authority must not write, but the card is staged, not lost (I5): it is delivered once
        # the operator reconfigures this node as a replica of the current authority.
        log.error("staff.run_link: %s is fenced (%s); staging the run card in the outbox", run.id, exc)
        if getattr(run, "thread_id", ""):
            enqueue_run_card(run, status, question=question, summary=summary)
        return None
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
            "needs_evidence",
        ):
            # Typed evidence decides, never the summary prose (#1808).
            artifact = getattr(run, "artifact", "") or ""
            target = work_item_state_for_success(artifact)
            if target != wi.state:
                wi_store.transition_state(
                    work_item_id,
                    target,
                    actor=run.role,
                    reason=f"Run {run.id} succeeded with artifact {artifact or 'none'}",
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

    payload = relay_payload(run, status, question, summary)
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
    if status in claim_guard.TERMINAL_STATUSES and run.target_kind == "issue":
        # Drop the claim label this run applied (#2562); off-thread so a slow `gh` never delays the status change.
        threading.Thread(target=claim_guard.release_claim, args=(run.id, status), daemon=True).start()
    config = get_authority_config()
    if config.is_replica and run.thread_id:
        # A replica never commits a conversation write: stage the update for the authority (#1800).
        update_linked_work_item(run, status, summary=summary)
        enqueue_run_card(run, status, question=question, summary=summary)
        return None
    update_linked_work_item(run, status, summary=summary)
    card = post_run_card(
        run,
        status=status,
        question=question,
        summary=summary,
        store=store,
        bus=bus,
        authority_epoch=config.epoch if config.is_authority else 0,
        authority_node=config.node if config.is_authority else "",
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


def apply_relayed_run_card(
    store: Any, bus: Any, thread_id: str, req: Any
) -> tuple[MessageRecord | None, ApplyResult | None]:
    """Apply a relayed run-card update received from an executing peer node.

    Pre: ``req.version`` / ``req.epoch`` are optional (legacy peers omit them). Post: ``(card, result)``;
    raises :class:`StaleEpochError` when the sender's epoch is not this authority's.
    """
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
    return post_run_card_checked(
        run_stub,
        status=req.status,
        text=req.body_md or "",
        question=req.question,
        summary=req.summary,
        store=store,
        bus=bus,
        version=getattr(req, "version", None),
        epoch=getattr(req, "epoch", None),
        authority_epoch=get_authority_config().epoch,
        authority_node=get_authority_config().node,
    )
