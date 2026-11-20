"""Carry an exact-head review PASS across main-only merges; cap fix passes (Part of #4563).

Pure decision logic. Callers supply per-head diff fingerprints, i.e. (path, git mode, blob oid)
of the PR diff against ``main``. A PASS carries forward only when the fingerprint of the
reviewed head equals the current head's, so real code changes (including mode changes) still
need a fresh exact-head review.

Only ``fix_pass_decision`` is operative. ``diff_fingerprint`` and ``pass_with_carry_forward``
are ADVISORY and deliberately NOT wired into arming: ADR 0010 requires fresh independent
acceptance whenever the integration tree changes, so any arming use needs an explicit ADR
amendment first (panel th_2f46d79d092d, Option C).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Literal

from coordination.merge_shepherd_exact_head import (
    DEFAULT_REVIEW_PASS_TTL_HOURS,
    HeadReviewPass,
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


def fix_pass_decision(*, failed_fix_passes: int) -> Literal["fix", "escalate"]:
    """Allow ``MAX_FIX_PASSES`` fix attempts, then escalate to human/Barb review."""
    if failed_fix_passes < 0:
        raise ValueError("failed_fix_passes must be >= 0")
    return "fix" if failed_fix_passes < MAX_FIX_PASSES else "escalate"
