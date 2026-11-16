"""Pre-terminal verification fold (#4269): enforce before the lifecycle terminal write."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from staff.store import RunRecord
from staff.verification import (
    UNVERIFIED_OUTPUT,
    deliverable_failure_updates,
    evaluate,
    verify_mode,
)
from staff.verification_types import PrProbe, Verdict


def apply_enforce_before_terminal(
    rec: RunRecord,
    *,
    classified_status: str,
    failure_class: str,
    retryable: bool,
    error: str,
    remediation: str,
    opens_pr: bool,
    probe: PrProbe,
    mode: str | None = None,
    now: datetime | None = None,
    result_text: str = "",
) -> tuple[str, str, bool, str, str, Verdict | None]:
    """Fold enforce/deliverable outcomes into the first terminal transition (#4269)."""
    mode = mode or verify_mode()
    if classified_status != "succeeded" or not opens_pr or mode == "off":
        return classified_status, failure_class, retryable, error, remediation, None
    evaluation = evaluate(
        replace(rec, status=classified_status),
        opens_pr=True,
        probe=probe,
        now=now or datetime.now(UTC),
        result_text=result_text,
    )
    if mode == "enforce" and evaluation.verification == "failed":
        return "failed", UNVERIFIED_OUTPUT, False, evaluation.detail, remediation, evaluation
    deliverable = deliverable_failure_updates(
        rec,
        evaluation,
        opens_pr=opens_pr,
        execution_status=classified_status,
    )
    if deliverable:
        return (
            str(deliverable.get("status", classified_status)),
            str(deliverable.get("failure_class", failure_class)),
            bool(deliverable.get("retryable", retryable)),
            str(deliverable.get("error", error)),
            str(deliverable.get("remediation", remediation)),
            evaluation,
        )
    return classified_status, failure_class, retryable, error, remediation, evaluation
