"""Carry an exact-head review PASS across main-only merges; cap fix passes (Part of #4563).

Pure decision logic. Callers supply per-head diff fingerprints, i.e. (path, git mode, blob oid)
of the PR diff against ``main``. A PASS carries forward only when the fingerprint of the
reviewed head equals the current head's, so real code changes (including mode changes) still
need a fresh exact-head review.

``fix_pass_decision`` and ``failed_fix_passes_from_reviews`` are operative.
``diff_fingerprint`` and ``pass_with_carry_forward`` are ADVISORY and deliberately NOT
wired into arming: ADR 0010 requires fresh independent acceptance whenever the
integration tree changes, so any arming use needs an explicit ADR amendment first
(panel th_2f46d79d092d, Option C).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
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
    known = tuple(row.head_sha for row in passes if row.pr == pr)
    dated: list[tuple[datetime, HeadReviewPass]] = []
    for row in passes:
        if row.pr != pr:
            continue
        stamp = _parse_recorded_at(row.recorded_at)
        if stamp is None:
            continue
        dated.append((stamp, row))
    dated.sort(key=lambda item: item[0])

    latest: dict[str, tuple[datetime, HeadReviewPass]] = {}
    for stamp, row in dated:
        key = ""
        for existing in latest:
            if head_sha_matches(existing, row.head_sha, known_shas=known):
                key = existing
                break
        if not key:
            key = row.head_sha.strip().lower()
        if not key:
            continue
        previous = latest.get(key)
        if previous is None or stamp >= previous[0]:
            latest[key] = (stamp, row)

    failed = 0
    awaiting_fix = False
    for _, row in sorted(latest.values(), key=lambda item: item[0]):
        if row.verdict.strip().lower() == "approve":
            failed = 0
            awaiting_fix = False
            continue
        if awaiting_fix:
            failed += 1
        awaiting_fix = True
    return failed


def fix_pass_decision(*, failed_fix_passes: int) -> Literal["fix", "escalate"]:
    """Allow ``MAX_FIX_PASSES`` fix attempts, then escalate to human/Barb review."""
    if failed_fix_passes < 0:
        raise ValueError("failed_fix_passes must be >= 0")
    return "fix" if failed_fix_passes < MAX_FIX_PASSES else "escalate"
