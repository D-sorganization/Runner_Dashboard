"""Launch-time guard for cloud/agy/gemini dispatch (#3962 S1).

Cursor cloud (and agy/gemini) launches have landed on issues that already have
an open PR from another lane, or on drained/held repos — measured
2026-10-27..11-03: cursor 210 PRs, 53 closed unmerged (25%) vs 0-4% for every
other lane. This module is the refuse-with-reason check a dispatch path calls
before launching; wiring it into the cloud dispatch path (S2) and the agy/gemini
lane keepers (S3) is out of scope here.

No second claim store or hold list: this is a thin adapter over
``coordination.eligibility.is_eligible`` (#3976 S1), which itself composes
``coordination.claims.check`` (already folds "has an open PR" and "claimed by
another lane" into its ``held`` flag; RM's lease TTL already expires a stale
claim, so there is no separate age check to reinvent) with a holds fetcher
this module supplies — the same ``staff.holds.HoldsList.blocking`` convention
``backend/escalation/merge_jump.py`` uses for "is this repo under a hold".
Both signals fail closed: an unanswerable claim check or a hold list that
cannot be read must never be treated as "free to launch".
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from coordination import claims as coordination_claims
from coordination import eligibility as coordination_eligibility
from staff.holds import Hold, HoldsList, matches

logger = logging.getLogger(__name__)

ClaimCheck = Callable[[str, int], dict[str, Any]]


@dataclass(frozen=True)
class LaunchGuardVerdict:
    """Whether a launch may proceed, and the logged reason either way."""

    allowed: bool
    reason: str


def _first_blocking_hold(repo: str, holds: Sequence[Hold]) -> Hold | None:
    """Same rule as ``HoldsList.blocking``: first active schedule-kind hold matching this repo."""
    return next((h for h in holds if h.kind == "schedule" and matches(h, "*", repo)), None)


def _reason(verdict: coordination_eligibility.EligibilityVerdict) -> str:
    if verdict.reasons:
        return verdict.reasons[0]
    if verdict.unknowns:
        unknown = verdict.unknowns[0]
        return f"{unknown.signal} unknown: {unknown.detail}"
    return "eligible" if verdict.allowed else "ineligible"


def check_launch_allowed(
    repo: str,
    issue: int,
    *,
    holds: Sequence[Hold] | None = None,
    claim_check: ClaimCheck | None = None,
) -> LaunchGuardVerdict:
    """Refuse a launch on a drained/held repo, a claimed issue, or an unanswerable check.

    ``holds`` lets a caller supply a pre-fetched list (e.g. for a batch of
    launches); when omitted, this fetches fresh from ``HoldsList().blocking``.
    ``claim_check`` defaults to ``coordination.claims.check`` (looked up at
    call time, not import time, so tests can monkeypatch it in place).
    """
    resolved_claim_check = claim_check if claim_check is not None else coordination_claims.check

    def holds_fetch(target_repo: str) -> str | None:
        hold = _first_blocking_hold(target_repo, holds) if holds is not None else HoldsList().blocking("*", target_repo)
        return hold.text if hold is not None else None

    cache = coordination_eligibility.EligibilityCache(claim_check=resolved_claim_check, holds_fetch=holds_fetch)
    verdict = coordination_eligibility.is_eligible(repo, issue, "*", cache=cache)
    reason = _reason(verdict)
    if not verdict.allowed:
        logger.info("launch guard refused %s#%s: %s", repo, issue, reason)
    return LaunchGuardVerdict(allowed=verdict.allowed, reason=reason)
