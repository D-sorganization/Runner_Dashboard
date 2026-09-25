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
    """Post or update a run_card message in the linked conversation thread."""
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
    }

    msg_id = f"msg-card-{run.id}-{status}"
    try:
        saved = conv_store.add_message(
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
        if hasattr(event_bus, "publish_sync"):
            event_bus.publish_sync(thread_id, "run_card", saved.to_dict())
        return saved
    except Exception as exc:  # noqa: BLE001
        log.warning("staff.run_link: failed to post run card for %s: %s", run.id, exc)
        return None


def handle_run_status_change(
    run: RunRecord,
    status: str,
    question: str | None = None,
    summary: str | None = None,
    store: Any = None,
    bus: Any = None,
) -> MessageRecord | None:
    """Handler called on run transition (queued, running, needs_input, succeeded, failed)."""
    return post_run_card(
        run,
        status=status,
        question=question,
        summary=summary,
        store=store,
        bus=bus,
    )


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
    return rec
