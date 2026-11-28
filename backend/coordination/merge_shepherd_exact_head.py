"""Exact-head independent review PASS gating for merge shepherd arming (Part of #4249)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from coordination.merge_shepherd_holds import MERGE_SHEPHERD_HOLD_LABELS, normalize_hold_label

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


def head_sha_matches(recorded: str, head: str, *, known_shas: Sequence[str] = ()) -> bool:
    """True when ``recorded`` matches ``head`` by full SHA or a unique short prefix."""
    rec, act = _normalize_sha(recorded), _normalize_sha(head)
    if not rec or not act:
        return False
    if rec == act:
        return True
    shorter, longer = (rec, act) if len(rec) <= len(act) else (act, rec)
    if len(shorter) < 7 or not longer.startswith(shorter):
        return False
    pool = {_normalize_sha(s) for s in known_shas if _normalize_sha(s)}
    pool.update({rec, act})
    full_shas = {s for s in pool if len(s) == 40}
    if not full_shas:
        return True
    prefix = shorter
    matches = [s for s in full_shas if s.startswith(prefix)]
    return len(matches) == 1


def _parse_recorded_at(stamp: str) -> datetime | None:
    text = stamp.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
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
    """Return whether the latest verdict at ``head_sha`` is a fresh approve."""
    known_shas = tuple(row.head_sha for row in passes)
    at_head = [
        row for row in passes if row.pr == pr and head_sha_matches(row.head_sha, head_sha, known_shas=known_shas)
    ]
    if not at_head:
        return False, "missing_pass_at_head"
    dated = [(row, _parse_recorded_at(row.recorded_at)) for row in at_head]
    if any(stamp is None for _, stamp in dated):
        return False, "pass_timestamp_unparseable"
    latest, recorded = max(
        dated, key=lambda item: (item[1] or datetime.min.replace(tzinfo=UTC), item[0].verdict != "approve")
    )
    if latest.verdict != "approve":
        return False, "rejected_at_head"
    if recorded is None:
        return False, "pass_timestamp_unparseable"
    clock = now if now is not None else datetime.now(tz=UTC)
    if clock - recorded > timedelta(hours=max(1, ttl_hours)):
        return False, "stale_unverified"
    return True, "exact_head_pass"


def _provider_family(provider: str) -> str:
    from staff.review import PROVIDER_FAMILY

    value = provider.strip().lower()
    known = {*PROVIDER_FAMILY.values(), "deepseek", "fleet"}
    return PROVIDER_FAMILY.get(value, value if value in known else "")


def _login_provider(login: str) -> str:
    """Only known authenticated fleet App logins carry provider provenance."""
    lower = login.lower()
    if lower == "d-sorggeneralagent[bot]":
        return "fleet"
    match = re.fullmatch(r"d-sorg(codex|claude|gemini|agy|cursor|deepseek)agent\[bot\]", lower)
    return {"agy": "antigravity", "cursor": "cursor-agent"}.get(match[1], match[1]) if match else ""


def _independent(reviewer: str, author: str) -> bool:
    reviewer_family, author_family = _provider_family(reviewer), _provider_family(author)
    return bool(reviewer_family and author_family and reviewer_family != author_family)


def _run_provider(rec: object) -> str:
    """Use the successful executor, rather than the initially requested provider."""
    chain = getattr(rec, "provider_chain", "")
    if chain:
        try:
            attempts = json.loads(chain)
        except (ValueError, TypeError):
            return ""
        if not isinstance(attempts, list) or not attempts or not isinstance(attempts[-1], dict):
            return ""
        last = attempts[-1]
        return str(last.get("provider") or "") if last.get("outcome") == "succeeded" else ""
    return str(getattr(rec, "provider", "") or "")


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
    if any(normalize_hold_label(lbl) in MERGE_SHEPHERD_HOLD_LABELS for lbl in labels):
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
    runs = list_runs(limit=500)
    authors: dict[int, set[str]] = {}
    for rec in runs:
        rec_role = str(getattr(rec, "role", ""))
        if (
            getattr(rec, "repo", "") != repo
            or rec_role == role
            or rec_role.startswith(("acceptance-review", "code-reviewer"))
        ):
            continue
        pr = getattr(rec, "pr_number", None)
        if pr:
            authors.setdefault(int(pr), set()).add(_provider_family(_run_provider(rec)))
    for rec in runs:
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
        if verdict.status != "succeeded":
            verdict = parse_review_verdict(str(getattr(rec, "last_line", "") or ""))
        if verdict.status != "succeeded" or not verdict.pr_number or not verdict.head_sha:
            continue
        if verdict.verdict not in {"approve", "changes", "escalate"}:
            continue
        mapped = "approve" if verdict.verdict == "approve" else "reject"
        reviewer = _run_provider(rec)
        author_families = authors.get(int(verdict.pr_number), set())
        if mapped == "approve" and (
            len(author_families) != 1 or not all(_independent(reviewer, a) for a in author_families)
        ):
            continue
        if getattr(rec, "pr_number", None) not in (None, verdict.pr_number):
            continue
        rows.append(
            HeadReviewPass(
                pr=int(verdict.pr_number),
                head_sha=verdict.head_sha,
                verdict=mapped,
                recorded_at=str(getattr(rec, "ended_at", "") or getattr(rec, "created_at", "") or ""),
                reviewer_model=str(getattr(rec, "model", "") or ""),
            )
        )
    return tuple(rows)


def _strip_quoted_text(body: str) -> str:
    """Exclude markdown blockquotes, code blocks, and quoted verdict mentions (not prose contractions)."""
    if not body:
        return ""
    # Remove fenced code blocks
    text = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
    # Remove blockquote lines
    text = re.sub(r"(?m)^\s*>.*$", "", text)
    # Remove only quoted/backticked FIX-NEEDED or PASS @sha mentions (not arbitrary apostrophe prose).
    verdict_in_quotes = (
        r"(?<!Verdict:\s)(?<!Verdict: )"
        r'["`][^"\n`]*(?:FIX-NEEDED|PASS)\s+@[0-9a-fA-F]{7,40}[^"\n`]*["`]'
    )
    text = re.sub(verdict_in_quotes, "", text, flags=re.IGNORECASE)
    return text


def collect_review_passes_from_comments(
    comments: Sequence[Mapping[str, Any]],
    pr: int,
    *,
    author_login: str = "",
) -> tuple[HeadReviewPass, ...]:
    """Parse PASS records from PR issue comments carrying STAFF_RESULT approve verdicts."""
    from staff.review import parse_review_verdict

    rows: list[HeadReviewPass] = []
    for c in comments:
        if not isinstance(c, Mapping):
            continue
        body = str(c.get("body") or "")
        unquoted = _strip_quoted_text(body)
        # This is the publisher's visible comment format, not a STAFF_RESULT line.
        if "Independent Exact-Head Acceptance Review" in unquoted:
            # Bold heading form (#4708): ``**Independent ... — FIX-NEEDED** @sha``.
            match = re.search(
                r"(?:Verdict:\s*[`'\"]?|^\s*|Blocking:\s*)(PASS|FIX-NEEDED)\s+@([0-9a-fA-F]{7,40})\b"
                r"|\*\*[^*\n]*?\b(PASS|FIX-NEEDED)\*\*\s*@([0-9a-fA-F]{7,40})\b",
                unquoted,
                re.IGNORECASE | re.MULTILINE,
            )
            if match:
                plain = match.group(1) is not None
                verdict_word = match.group(1) if plain else match.group(3)
                sha = match.group(2) if plain else match.group(4)
                decision = "approve" if verdict_word.upper() == "PASS" else "changes"
                body = f"STAFF_RESULT: review {decision} #{pr} @{sha}"
        verdict = parse_review_verdict(body)
        if verdict.status != "succeeded" or not verdict.head_sha:
            continue
        if verdict.pr_number and int(verdict.pr_number) != pr:
            continue
        if verdict.verdict not in {"approve", "changes", "escalate"}:
            continue
        mapped = "approve" if verdict.verdict == "approve" else "reject"
        reviewer_login = str((c.get("author") or {}).get("login") or "")
        if mapped == "approve" and not _independent(_login_provider(reviewer_login), _login_provider(author_login)):
            continue
        rows.append(
            HeadReviewPass(
                pr=pr,
                head_sha=verdict.head_sha,
                verdict=mapped,
                recorded_at=str(c.get("createdAt") or ""),
                reviewer_model=reviewer_login,
            )
        )
    return tuple(rows)


def _commented_review_is_fix_needed(body: str, commit_sha: str) -> bool:
    """True only for structured FIX-NEEDED verdicts at ``commit_sha``, not quoted mentions."""
    if not body:
        return False
    from staff.review import parse_review_verdict

    unquoted = _strip_quoted_text(body)
    if "Independent Exact-Head Acceptance Review" in unquoted:
        match = re.search(
            r"(?:Verdict:\s*[`'\"]?|^\s*|Blocking:\s*)FIX-NEEDED\s+@([0-9a-fA-F]{7,40})\b",
            unquoted,
            re.IGNORECASE | re.MULTILINE,
        )
        if match:
            return head_sha_matches(match.group(1), commit_sha)
    verdict = parse_review_verdict(unquoted)
    if verdict.status == "succeeded" and verdict.verdict in {"changes", "escalate"}:
        return not verdict.head_sha or head_sha_matches(verdict.head_sha, commit_sha)
    match = re.search(
        r"(?:Verdict:\s*[`'\"]?|^\s*|Blocking:\s*)FIX-NEEDED\s+@([0-9a-fA-F]{7,40})\b",
        unquoted,
        re.IGNORECASE | re.MULTILINE,
    )
    if match:
        return head_sha_matches(match.group(1), commit_sha)
    # After quoted mentions are stripped, a bound FIX-NEEDED @sha is a real verdict (not prose).
    match = re.search(
        r"\bFIX-NEEDED\s+@([0-9a-fA-F]{7,40})\b",
        unquoted,
        re.IGNORECASE,
    )
    if match:
        return head_sha_matches(match.group(1), commit_sha)
    return False


def collect_review_passes_from_github_reviews(
    reviews: Sequence[Mapping[str, Any]],
    pr: int,
    *,
    author_login: str = "",
) -> tuple[HeadReviewPass, ...]:
    """Build verdict records from GitHub PR reviews (APPROVED, CHANGES_REQUESTED, or COMMENTED FIX-NEEDED)."""
    rows: list[HeadReviewPass] = []
    for r in reviews:
        if not isinstance(r, Mapping):
            continue
        state = str(r.get("state") or "").upper()
        if state not in {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}:
            continue
        commit = r.get("commit") or {}
        sha = str(commit.get("oid") or "") if isinstance(commit, Mapping) else ""
        if not sha:
            continue
        if state == "APPROVED":
            mapped = "approve"
        elif state == "CHANGES_REQUESTED":
            mapped = "reject"
        elif _commented_review_is_fix_needed(str(r.get("body") or ""), sha):
            mapped = "reject"
        else:
            continue
        reviewer_login = str((r.get("author") or {}).get("login") or "")
        if mapped == "approve" and not _independent(_login_provider(reviewer_login), _login_provider(author_login)):
            continue
        rows.append(
            HeadReviewPass(
                pr=pr,
                head_sha=sha,
                verdict=mapped,
                recorded_at=str(r.get("submittedAt") or ""),
                reviewer_model=reviewer_login,
            )
        )
    return tuple(rows)
