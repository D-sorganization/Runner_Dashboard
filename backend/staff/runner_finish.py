"""Terminal classification, verification fold, and notifier hooks for staff runs."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from staff.execution_result import classify_execution_result
from staff.plan import RunPlan
from staff.run_status import RunStatus, transition_to
from staff.store import RunRecord, RunStore, _now

from staff import consolidation, review, verification
from staff import runner_ops as ops
from staff import usage as usage_mod

if TYPE_CHECKING:
    from staff.runner import StaffRunner


def finish_run_after_execution(
    runner: StaffRunner,
    *,
    store: RunStore,
    rec: RunRecord,
    plan: RunPlan,
    workdir: Path,
    cancelled: bool,
    rc: int,
    usage: Any,
    result_line: str,
    transcript: Path,
    question: str | None,
    watchdog_failure_class: str = "",
    watchdog_error: str = "",
) -> None:
    """Classify exit, apply pre-terminal verify, transition, and emit terminal notifier."""
    from staff import workspace  # noqa: PLC0415

    status, failure_class, retryable, remediation, error = classify_execution_result(
        cancelled=cancelled,
        rc=rc,
        result_line=result_line,
        provider=plan.provider,
        transcript_path=transcript,
        watchdog_failure_class=watchdog_failure_class,
        watchdog_error=watchdog_error,
        machine=runner.machine,
        has_thread=bool(rec.thread_id),
        run=rec,
        opens_pr=runner._opens_pr_or_false(rec.role),
        probe=verification.GhCliPrProbe(),
    )
    same_provider = review.is_same_provider_review(rec.prompt)
    status, failure_class, retryable, error, remediation = ops.apply_code_reviewer_post_classification(
        store, rec, plan, rc, status, failure_class, retryable, error, remediation, result_line, transcript
    )
    workspace.persist_run_transcript(store, rec.id, workdir)
    opens_pr = runner.opens_pr(rec.role)
    pre_terminal_verdict: verification.Verdict | None = None
    if status == "succeeded" and not cancelled:
        (
            status,
            failure_class,
            retryable,
            error,
            remediation,
            pre_terminal_verdict,
        ) = verification.apply_enforce_before_terminal(
            rec,
            classified_status=status,
            failure_class=failure_class,
            retryable=retryable,
            error=error,
            remediation=remediation,
            opens_pr=opens_pr,
            probe=verification.GhCliPrProbe(),
            result_text=result_line,
        )
    store.append_event(rec.id, "exit", f"exit code {rc} → {status}")
    transition_to(
        store,
        rec.id,
        RunStatus(status),
        notify_terminal=False,  # emitted below, once verification has recorded the artifact
        failure_class=failure_class,
        retryable=retryable,
        remediation=remediation,
        error=error,
        ended_at=_now(),
        exit_code=rc,
        cost_usd=float(usage.get("cost_usd", 0.0)),
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
        outcome=review.parse_outcome(result_line, same_provider=same_provider)
        or consolidation.parse_outcome(result_line),
    )
    usage_mod.finalize_cost(store, rec.id, plan.provider, plan.model)
    if pre_terminal_verdict is None:
        verification.verify_and_record(store, rec.id, opens_pr=lambda: opens_pr)
    else:
        verification.verify_and_record(
            store,
            rec.id,
            opens_pr=lambda: opens_pr,
            precomputed_verdict=pre_terminal_verdict,
        )
    updated_rec = store.get_run(rec.id)
    if updated_rec is not None:
        ops.emit_terminal_run_status(
            store,
            updated_rec,
            plan,
            status=updated_rec.status,
            question=question,
            result_line=result_line,
            cancelled=cancelled,
            rc=rc,
            transcript=transcript,
        )
