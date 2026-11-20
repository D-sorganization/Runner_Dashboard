"""Exact-head independent review PASS gating for merge shepherd arming (Part of #4249)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from coordination.merge_shepherd_low_risk import LOW_RISK_BLOCKED_LABELS

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
    """Arming requires an exact-head PASS at ``head_sha`` (no docs/tests-only shortcut)."""
    if str(checks_state).upper() != "SUCCESS":
        return "no_approval", "required_checks_not_green"
    if any(str(lbl).strip().casefold() in LOW_RISK_BLOCKED_LABELS for lbl in labels):
        return "no_approval", "blocked_label"
    if not files_complete:
        return "no_approval", "files_incomplete"
    if not file_paths:
        return "no_approval", "no_changed_files"

    ok, pass_detail = pass_at_head(
        review_passes,
        pr=pr_number,
        head_sha=head_sha,
        now=now,
        ttl_hours=review_pass_ttl_hours,
    )
    if ok:
        decision_label = (
            "review_decision_approved" if str(review_decision or "").upper() == "APPROVED" else "exact_head_verdict"
        )
        return "reviewed", f"{decision_label}+{pass_detail}"

    if str(review_decision or "").upper() == "APPROVED":
        return "no_approval", pass_detail

    return "no_approval", pass_detail


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
        if getattr(rec, "repo", "") != repo:
            continue
        rec_role = getattr(rec, "role", "")
        if (
            rec_role != role
            and not rec_role.startswith("acceptance-review")
            and not rec_role.startswith("code-reviewer")
        ):
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


def collect_review_passes_from_comments(
    comments: Sequence[Mapping[str, Any]],
    pr: int,
) -> tuple[HeadReviewPass, ...]:
    """Parse PASS records from PR issue comments carrying STAFF_RESULT approve verdicts."""
    from staff.review import parse_review_verdict

    rows: list[HeadReviewPass] = []
    for c in comments:
        if not isinstance(c, Mapping):
            continue
        body = str(c.get("body") or "")
        verdict = parse_review_verdict(body)
        if verdict.status == "succeeded" and verdict.verdict == "approve":
            if verdict.pr_number and int(verdict.pr_number) != pr:
                continue
            if not verdict.head_sha:
                continue
            author_login = str((c.get("author") or {}).get("login") or "")
            rows.append(
                HeadReviewPass(
                    pr=pr,
                    head_sha=verdict.head_sha,
                    verdict="approve",
                    recorded_at=str(c.get("createdAt") or ""),
                    reviewer_model=author_login,
                )
            )
    return tuple(rows)


def collect_review_passes_from_github_reviews(
    reviews: Sequence[Mapping[str, Any]],
    pr: int,
) -> tuple[HeadReviewPass, ...]:
    """Build PASS records from formal GitHub PR reviews in state APPROVED with commit SHA."""
    rows: list[HeadReviewPass] = []
    for r in reviews:
        if not isinstance(r, Mapping):
            continue
        if str(r.get("state") or "").upper() != "APPROVED":
            continue
        commit = r.get("commit") or {}
        sha = str(commit.get("oid") or "") if isinstance(commit, Mapping) else ""
        if not sha:
            continue
        author_login = str((r.get("author") or {}).get("login") or "")
        rows.append(
            HeadReviewPass(
                pr=pr,
                head_sha=sha,
                verdict="approve",
                recorded_at=str(r.get("submittedAt") or ""),
                reviewer_model=author_login,
            )
        )
    return tuple(rows)
