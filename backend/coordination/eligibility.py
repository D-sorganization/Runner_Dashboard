"""Shared launcher-eligibility verdict (#3976, slice 1 of #2781's design-of-record).

Every launcher (Job Board, ``staff.launch_guard``, eventually the staff scheduler)
needs the same answer to "may this lane start this issue right now?" Today each
caller reimplements its own slice of that check. This module is the single
read-only ``is_eligible(repo, issue, lane)`` they will all delegate to.

Dependency direction: ``staff/`` -> ``coordination/``, never the reverse — this
module only depends on ``coordination.claims`` for claim/open-PR state. Holds and
advisory signals (path overlap, staleness — roadmap items for later slices) are
supplied by the caller through ``EligibilityCache``'s injected fetchers, so this
module never needs to know ``staff.holds`` exists.

Pure/read-only: ``is_eligible`` never writes a lease and never mints a fence
(fencing lives only in #2784's atomic claim). Fail-closed by tier: an unreadable
**core** signal (holds, claim state, open-PR) makes the issue ineligible with a
severity-tagged entry in ``unknowns``; an unreadable **advisory** signal still
allows, with the uncertainty surfaced as a warning instead of a refusal. Callers
(e.g. the Job Board) must never render a core unknown as "claimable".
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from coordination import claims as coordination_claims

ClaimCheck = Callable[[str, int], dict[str, Any]]
HoldsFetch = Callable[[str], str | None]


@dataclass(frozen=True)
class Unknown:
    """A signal ``is_eligible`` could not read, with its fail-closed-or-open tier."""

    signal: str
    severity: str  # "core" | "advisory"
    detail: str


@dataclass(frozen=True)
class EligibilityVerdict:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    unknowns: list[Unknown] = field(default_factory=list)


class EligibilityCache:
    """Memoizes one dispatch cycle's GitHub lookups.

    A dispatch cycle calls ``is_eligible`` once per candidate issue, often for the
    same repo and sometimes the same issue across lanes; sharing one cache instance
    across those calls means the cycle queries claim state and holds once each,
    not once per call.
    """

    def __init__(
        self,
        *,
        claim_check: ClaimCheck | None = None,
        holds_fetch: HoldsFetch | None = None,
    ) -> None:
        self._claim_check = claim_check if claim_check is not None else coordination_claims.check
        self._holds_fetch = holds_fetch if holds_fetch is not None else (lambda _repo: None)
        self._claims: dict[tuple[str, int], dict[str, Any]] = {}
        self._holds: dict[str, str | None] = {}

    def claim_status(self, repo: str, issue: int) -> dict[str, Any]:
        key = (repo, issue)
        if key not in self._claims:
            self._claims[key] = self._claim_check(repo, issue)
        return self._claims[key]

    def blocking_hold(self, repo: str) -> str | None:
        if repo not in self._holds:
            self._holds[repo] = self._holds_fetch(repo)
        return self._holds[repo]


AdvisoryCheck = Callable[[str, int, str, EligibilityCache], Unknown | None]


def is_eligible(
    repo: str,
    issue: int,
    lane: str,
    *,
    cache: EligibilityCache | None = None,
    advisory_checks: Sequence[AdvisoryCheck] = (),
) -> EligibilityVerdict:
    """Read-only verdict for whether ``lane`` may start work on ``repo``#``issue``.

    ``lane`` is accepted now (not yet used by any check in this slice) so callers
    and tests have a stable signature ahead of the lane-policy signals roadmap
    items 6-7 add later.
    """
    cache = cache if cache is not None else EligibilityCache()
    unknowns: list[Unknown] = []

    try:
        hold = cache.blocking_hold(repo)
    except Exception as exc:  # noqa: BLE001 - an unanswerable hold check is a refusal
        return EligibilityVerdict(
            allowed=False,
            reasons=[f"holds check raised: {exc}"],
            unknowns=[Unknown(signal="holds", severity="core", detail=str(exc))],
        )
    if hold is not None:
        return EligibilityVerdict(allowed=False, reasons=[f"repo held: {hold}"], unknowns=unknowns)

    try:
        status = cache.claim_status(repo, issue)
    except Exception as exc:  # noqa: BLE001 - an unanswerable claim check is a refusal
        return EligibilityVerdict(
            allowed=False,
            reasons=[f"claim check raised: {exc}"],
            unknowns=[Unknown(signal="claim", severity="core", detail=str(exc))],
        )

    if not status.get("available", False):
        detail = str(status.get("reason") or "no reason given")
        return EligibilityVerdict(
            allowed=False,
            reasons=[f"claim status unavailable: {detail}"],
            unknowns=[Unknown(signal="claim", severity="core", detail=detail)],
        )

    if status.get("held"):
        reason = status.get("reason") or "held"
        return EligibilityVerdict(
            allowed=False,
            reasons=[f"issue already claimed or has an open PR: {reason}"],
            unknowns=unknowns,
        )

    for check in advisory_checks:
        try:
            unknown = check(repo, issue, lane, cache)
        except Exception as exc:  # noqa: BLE001 - advisory signals fail open, not closed
            signal = getattr(check, "__name__", "advisory")
            unknowns.append(Unknown(signal=signal, severity="advisory", detail=str(exc)))
            continue
        if unknown is not None:
            unknowns.append(unknown)

    return EligibilityVerdict(allowed=True, reasons=["eligible"], unknowns=unknowns)
