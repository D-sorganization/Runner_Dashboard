"""Launch-time guard for cloud/agy/gemini dispatch (#3962 S1).

Cursor cloud (and agy/gemini) launches have landed on issues that already have
an open PR from another lane, or on drained/held repos — measured
2026-10-27..11-03: cursor 210 PRs, 53 closed unmerged (25%) vs 0-4% for every
other lane. This module is the refuse-with-reason check a dispatch path calls
before launching; wiring it into the cloud dispatch path (S2) and the agy/gemini
lane keepers (S3) is out of scope here.

No second claim store or hold list: this composes the two checks the fleet
already has —``staff.holds.HoldsList.blocking`` (the same convention
``backend/escalation/merge_jump.py`` uses for "is this repo under a hold") and
``coordination.claims.check`` (which already folds "has an open PR" and
"claimed by another lane" into its ``held`` flag; RM's lease TTL already
expires a stale claim, so there is no separate age check to reinvent). Both
checks fail closed: an unanswerable claim check or a hold list that cannot be
read must never be treated as "free to launch".
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from coordination import claims as coordination_claims
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
    hold = _first_blocking_hold(repo, holds) if holds is not None else HoldsList().blocking("*", repo)
    if hold is not None:
        reason = f"repo held: {hold.text}"
        logger.info("launch guard refused %s#%s: %s", repo, issue, reason)
        return LaunchGuardVerdict(allowed=False, reason=reason)

    resolved_claim_check = claim_check if claim_check is not None else coordination_claims.check
    try:
        status = resolved_claim_check(repo, issue)
    except Exception as exc:  # noqa: BLE001 - an unanswerable check is a refusal, not a crash
        reason = f"claim check raised: {exc}"
        logger.info("launch guard refused %s#%s: %s", repo, issue, reason)
        return LaunchGuardVerdict(allowed=False, reason=reason)

    if not status.get("available", False):
        reason = f"claim status unavailable: {status.get('reason') or 'no reason given'}"
        logger.info("launch guard refused %s#%s: %s", repo, issue, reason)
        return LaunchGuardVerdict(allowed=False, reason=reason)

    if status.get("held"):
        reason = f"issue already claimed or has an open PR: {status.get('reason') or 'held'}"
        logger.info("launch guard refused %s#%s: %s", repo, issue, reason)
        return LaunchGuardVerdict(allowed=False, reason=reason)

    return LaunchGuardVerdict(allowed=True, reason="eligible")
