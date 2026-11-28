"""Carry an exact-head review PASS across main-only merges; cap fix passes (Part of #4563).

Pure decision logic. Callers supply per-head diff fingerprints, i.e. (path, git mode, blob oid)
of the PR diff against ``main``. A PASS carries forward only when the fingerprint of the
reviewed head equals the current head's, so real code changes (including mode changes) still
need a fresh exact-head review.

``fix_pass_decision``, ``failed_fix_passes_from_reviews``, and
``failed_fix_passes_from_cycle`` are operative.
``diff_fingerprint`` and ``pass_with_carry_forward`` are ADVISORY and deliberately NOT
wired into arming: ADR 0010 requires fresh independent acceptance whenever the
integration tree changes, so any arming use needs an explicit ADR amendment first
(panel th_2f46d79d092d, Option C).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from coordination.merge_shepherd_exact_head import (
    DEFAULT_REVIEW_PASS_TTL_HOURS,
    HeadReviewPass,
    _parse_recorded_at,
    head_sha_matches,
    pass_at_head,
)

MAX_FIX_PASSES = 1
_CI_FAILURE_STATES = frozenset({"FAILURE", "ERROR"})
FIX_PASS_ESCALATION_MARK = "escalate:fix_pass_cap"
FIX_PASS_ESCALATION_AUDIENCE = "barb"


@dataclass(frozen=True)
class HeadCheckFailure:
    """One CI rollup failure for a PR head.

    ``recorded_at`` is the commit time. A row without a parseable time is not
    evidence of a failed fix.
    """

    pr: int
    head_sha: str
    recorded_at: str
    state: str


def diff_fingerprint(files: Iterable[tuple[str, str, str]]) -> str:
    """Order-independent digest of ``(path, git_mode, blob_oid)``; ``""`` for an empty diff.

    The mode is part of the identity: ``100644`` -> ``100755`` keeps the blob oid but
    changes runtime behavior, so it must never carry an old PASS.
    """
    rows = sorted({f"{path}\0{mode}\0{oid}" for path, mode, oid in files})
    if not rows:
        return ""
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def pass_with_carry_forward(
    passes: Sequence[HeadReviewPass],
    *,
    pr: int,
    head_sha: str,
    head_fingerprint: str,
    pass_fingerprints: Mapping[str, str],
    now: datetime | None = None,
    ttl_hours: int = DEFAULT_REVIEW_PASS_TTL_HOURS,
) -> tuple[bool, str]:
    """Exact-head PASS, else a fresh PASS whose reviewed diff equals the current diff."""
    ok, detail = pass_at_head(passes, pr=pr, head_sha=head_sha, now=now, ttl_hours=ttl_hours)
    if ok or not head_fingerprint:
        return ok, detail
    for row in passes:
        if row.pr != pr or row.verdict != "approve":
            continue
        if pass_fingerprints.get(row.head_sha, "") != head_fingerprint:
            continue
        fresh, _ = pass_at_head([row], pr=pr, head_sha=row.head_sha, now=now, ttl_hours=ttl_hours)
        if fresh:
            return True, "carried_forward_pass"
    return ok, detail


def failed_fix_passes_from_reviews(passes: Sequence[HeadReviewPass], *, pr: int) -> int:
    """Return failed fix attempts in the current open review cycle for ``pr``.

    The first non-approve head is the original failure. Each later non-approve head
    in that cycle is one failed fix pass. An approve closes the cycle. A later
    verdict on the same head replaces the earlier one. Rows with an unparseable
    timestamp are ignored, because they are not evidence of a failed fix.
    """
    return failed_fix_passes_from_cycle(passes, (), pr=pr)


def failed_fix_passes_from_cycle(
    passes: Sequence[HeadReviewPass],
    check_failures: Sequence[HeadCheckFailure],
    *,
    pr: int,
) -> int:
    """Return failed fix attempts from review rejections and CI failures.

    A head fails when its latest signal is a non-approve review or a CI failure.
    The first failed head is the original failure and does not consume the fix
    cap. Each later failed head is one failed fix pass. An approve closes the
    cycle. Review and CI signals on the same head count once. Rows with an
    unparseable timestamp are ignored.
    """
    known = tuple(row.head_sha for row in passes if row.pr == pr) + tuple(
        row.head_sha for row in check_failures if row.pr == pr
    )
    events: list[tuple[datetime, str, str]] = []
    for review in passes:
        if review.pr != pr:
            continue
        stamp = _parse_recorded_at(review.recorded_at)
        sha = review.head_sha.strip().lower()
        if stamp is None or not sha:
            continue
        kind = "approve" if review.verdict.strip().lower() == "approve" else "fail"
        events.append((stamp, sha, kind))
    for failure in check_failures:
        if failure.pr != pr or failure.state.strip().upper() not in _CI_FAILURE_STATES:
            continue
        stamp = _parse_recorded_at(failure.recorded_at)
        sha = failure.head_sha.strip().lower()
        if stamp is None or not sha:
            continue
        events.append((stamp, sha, "fail"))
    events.sort(key=lambda item: item[0])

    latest: dict[str, tuple[datetime, str]] = {}
    for stamp, sha, kind in events:
        key = ""
        for existing in latest:
            if head_sha_matches(existing, sha, known_shas=known):
                key = existing
                break
        if not key:
            key = sha
        previous = latest.get(key)
        if previous is None or stamp >= previous[0]:
            latest[key] = (stamp, kind)

    failed = 0
    awaiting_fix = False
    for _, kind in sorted(latest.values(), key=lambda item: item[0]):
        if kind == "approve":
            failed = 0
            awaiting_fix = False
            continue
        if awaiting_fix:
            failed += 1
        awaiting_fix = True
    return failed


def fix_pass_escalation_notice(*, repo: str, pr: int, failed_fix_passes: int, head_sha: str) -> str:
    """Barb-visible notice once the open cycle has used its one fix pass.

    Pre: ``pr`` is positive, ``failed_fix_passes`` has reached ``MAX_FIX_PASSES``,
    and ``repo`` and ``head_sha`` are non-empty.
    Post: the text starts with ``FIX_PASS_ESCALATION_MARK`` and names the audience.
    """
    if pr <= 0:
        raise ValueError("pr must be > 0")
    if failed_fix_passes < MAX_FIX_PASSES:
        raise ValueError("failed_fix_passes must be >= MAX_FIX_PASSES")
    repo_name = repo.strip()
    sha = head_sha.strip()
    if not repo_name or not sha:
        raise ValueError("repo and head_sha are required")
    return (
        f"{FIX_PASS_ESCALATION_MARK} {repo_name}#{pr} to {FIX_PASS_ESCALATION_AUDIENCE}: "
        f"{failed_fix_passes} failed fix pass(es) at {sha}. "
        "Human review required; merge shepherd will not auto-arm."
    )


def fix_pass_decision(*, failed_fix_passes: int) -> Literal["fix", "escalate"]:
    """Allow ``MAX_FIX_PASSES`` fix attempts, then escalate to human/Barb review."""
    if failed_fix_passes < 0:
        raise ValueError("failed_fix_passes must be >= 0")
    return "fix" if failed_fix_passes < MAX_FIX_PASSES else "escalate"
