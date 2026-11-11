"""Exact-head independent review PASS gating for merge shepherd arming (Part of #4249)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from coordination.merge_shepherd_low_risk import arm_eligibility_reason

DEFAULT_REVIEW_PASS_TTL_HOURS = 72


@dataclass(frozen=True)
class HeadReviewPass:
    pr: int
    head_sha: str
    verdict: str
    recorded_at: str
    reviewer_model: str = ""


HoldReasonFn = Callable[[str, int], str]


def _normalize_sha(sha: str) -> str:
    return sha.strip().lower()


def head_sha_matches(recorded: str, head: str) -> bool:
    """True when ``recorded`` matches ``head`` by full SHA or unique short prefix."""
    rec, act = _normalize_sha(recorded), _normalize_sha(head)
    if not rec or not act:
        return False
    if rec == act:
        return True
    shorter, longer = (rec, act) if len(rec) <= len(act) else (act, rec)
    return len(shorter) >= 7 and longer.startswith(shorter)


def _parse_recorded_at(stamp: str) -> datetime | None:
    text = stamp.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def pass_at_head(
    passes: Sequence[HeadReviewPass],
    *,
    pr: int,
    head_sha: str,
    now: datetime | None = None,
    ttl_hours: int = DEFAULT_REVIEW_PASS_TTL_HOURS,
) -> tuple[bool, str]:
    """Return whether an approve verdict exists for ``head_sha`` and is still fresh."""
    matches = [
        row for row in passes if row.pr == pr and row.verdict == "approve" and head_sha_matches(row.head_sha, head_sha)
    ]
    if not matches:
        return False, "missing_pass_at_head"
    latest = max(matches, key=lambda row: row.recorded_at)
    recorded = _parse_recorded_at(latest.recorded_at)
    if recorded is None:
        return False, "pass_timestamp_unparseable"
    clock = now if now is not None else datetime.now(tz=UTC)
    if clock - recorded > timedelta(hours=max(1, ttl_hours)):
        return False, "stale_unverified"
    return True, "exact_head_pass"


def shepherd_arm_eligibility(
    *,
    review_decision: str | None,
    file_paths: Sequence[str],
    additions: int,
    deletions: int,
    line_cap: int,
    labels: Sequence[str],
    checks_state: str,
    files_complete: bool,
    pr_number: int,
    head_sha: str,
    review_passes: Sequence[HeadReviewPass] = (),
    now: datetime | None = None,
    review_pass_ttl_hours: int = DEFAULT_REVIEW_PASS_TTL_HOURS,
) -> tuple[str, str]:
    """Docs/tests-only may arm without a hub pass; code paths need PASS at ``head_sha``."""
    kind, detail = arm_eligibility_reason(
        review_decision=review_decision,
        file_paths=file_paths,
        additions=additions,
        deletions=deletions,
        line_cap=line_cap,
        labels=labels,
        checks_state=checks_state,
        files_complete=files_complete,
    )
    if kind != "reviewed":
        return kind, detail
    ok, pass_detail = pass_at_head(
        review_passes,
        pr=pr_number,
        head_sha=head_sha,
        now=now,
        ttl_hours=review_pass_ttl_hours,
    )
    if not ok:
        return "no_approval", pass_detail
    return "reviewed", f"{detail}+{pass_detail}"


def collect_review_passes_from_store(
    store: object, repo: str, *, role: str = "code-reviewer"
) -> tuple[HeadReviewPass, ...]:
    """Build PASS records from succeeded code-reviewer runs with SHA-bound approve lines."""
    from staff.review import parse_review_verdict

    rows: list[HeadReviewPass] = []
    list_runs = getattr(store, "list_runs", None)
    if not callable(list_runs):
        return ()
    for rec in list_runs(limit=500):
        if getattr(rec, "repo", "") != repo or getattr(rec, "role", "") != role:
            continue
        if getattr(rec, "status", "") != "succeeded":
            continue
        text = str(getattr(rec, "outcome", "") or getattr(rec, "last_line", "") or "")
        verdict = parse_review_verdict(text)
        if verdict.status != "succeeded" or verdict.verdict != "approve" or not verdict.pr_number:
            continue
        if not verdict.head_sha:
            continue
        rows.append(
            HeadReviewPass(
                pr=int(verdict.pr_number),
                head_sha=verdict.head_sha,
                verdict="approve",
                recorded_at=str(getattr(rec, "ended_at", "") or getattr(rec, "created_at", "") or ""),
                reviewer_model=str(getattr(rec, "model", "") or ""),
            )
        )
    return tuple(rows)
