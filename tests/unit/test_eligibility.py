"""Shared launcher-eligibility verdict (#3976, slice 1 of #2781's design-of-record).

``is_eligible`` is the read-only module every launcher (Job Board, launch_guard, the
staff scheduler) will eventually share. This slice only wires it over the existing
``coordination.claims.check`` claim/open-PR signal; holds and advisory signals (path
overlap, staleness) are supplied by the caller via injected fetchers so this module
never needs to import ``staff.*`` (dependency direction: staff/ -> coordination/, never
the reverse).
"""

from __future__ import annotations

from typing import Any

import pytest
from coordination import claims as coordination_claims
from coordination.eligibility import EligibilityCache, EligibilityVerdict, Unknown, is_eligible

pytestmark = pytest.mark.unit


def _claim_check(available: bool, held: bool | None, reason: str = "") -> Any:
    def fn(repo: str, issue: int) -> dict[str, Any]:
        return {"available": available, "held": held, "agent": "", "session": "", "reason": reason}

    return fn


def _cache(*, claim_check: Any = None, holds_fetch: Any = None) -> EligibilityCache:
    return EligibilityCache(claim_check=claim_check, holds_fetch=holds_fetch)


def test_eligible_happy_path() -> None:
    cache = _cache(claim_check=_claim_check(True, False))
    verdict = is_eligible("Runner_Dashboard", 1234, "claude-a", cache=cache)
    assert verdict == EligibilityVerdict(allowed=True, reasons=["eligible"], unknowns=[])


def test_active_claim_is_ineligible() -> None:
    cache = _cache(claim_check=_claim_check(True, True, reason="held by claude-b"))
    verdict = is_eligible("Runner_Dashboard", 1234, "claude-a", cache=cache)
    assert verdict.allowed is False
    assert "held by claude-b" in verdict.reasons[0]


def test_open_pr_is_ineligible() -> None:
    # coordination.claims.check already folds "has an open PR" into its held flag.
    cache = _cache(claim_check=_claim_check(True, True, reason="held by claude-b, open PR #999"))
    verdict = is_eligible("Runner_Dashboard", 1234, "claude-a", cache=cache)
    assert verdict.allowed is False
    assert "open PR #999" in verdict.reasons[0]


def test_hold_is_ineligible() -> None:
    cache = _cache(claim_check=_claim_check(True, False), holds_fetch=lambda repo: "GM drain mode")
    verdict = is_eligible("Runner_Dashboard", 1234, "claude-a", cache=cache)
    assert verdict.allowed is False
    assert "GM drain mode" in verdict.reasons[0]


def test_unknown_hold_signal_is_ineligible_with_severity_tagged_unknown() -> None:
    def raising_holds_fetch(repo: str) -> str | None:
        raise RuntimeError("holds API down")

    cache = _cache(claim_check=_claim_check(True, False), holds_fetch=raising_holds_fetch)
    verdict = is_eligible("Runner_Dashboard", 1234, "claude-a", cache=cache)
    assert verdict.allowed is False
    assert verdict.unknowns == [Unknown(signal="holds", severity="core", detail="holds API down")]


def test_unknown_claim_signal_is_ineligible_with_severity_tagged_unknown() -> None:
    cache = _cache(claim_check=_claim_check(False, None, reason="error:Timeout"))
    verdict = is_eligible("Runner_Dashboard", 1234, "claude-a", cache=cache)
    assert verdict.allowed is False
    assert verdict.unknowns == [Unknown(signal="claim", severity="core", detail="error:Timeout")]


def test_claim_check_raising_is_ineligible_with_severity_tagged_unknown() -> None:
    def raising_check(repo: str, issue: int) -> dict[str, Any]:
        raise RuntimeError("boom")

    cache = _cache(claim_check=raising_check)
    verdict = is_eligible("Runner_Dashboard", 1234, "claude-a", cache=cache)
    assert verdict.allowed is False
    assert "boom" in verdict.reasons[0]
    assert verdict.unknowns == [Unknown(signal="claim", severity="core", detail="boom")]


def test_advisory_unknown_still_allows_with_a_surfaced_warning() -> None:
    def flaky_advisory(repo: str, issue: int, lane: str, cache: EligibilityCache) -> Unknown | None:
        raise RuntimeError("path-overlap lookup unavailable")

    cache = _cache(claim_check=_claim_check(True, False))
    verdict = is_eligible("Runner_Dashboard", 1234, "claude-a", cache=cache, advisory_checks=[flaky_advisory])
    assert verdict.allowed is True
    assert verdict.unknowns == [
        Unknown(signal="flaky_advisory", severity="advisory", detail="path-overlap lookup unavailable")
    ]


def test_advisory_check_result_surfaces_without_blocking() -> None:
    def stale_branch_advisory(repo: str, issue: int, lane: str, cache: EligibilityCache) -> Unknown | None:
        return Unknown(signal="staleness", severity="advisory", detail="branch pushed 9 days ago")

    cache = _cache(claim_check=_claim_check(True, False))
    verdict = is_eligible(
        "Runner_Dashboard", 1234, "claude-a", cache=cache, advisory_checks=[stale_branch_advisory]
    )
    assert verdict.allowed is True
    assert verdict.unknowns == [Unknown(signal="staleness", severity="advisory", detail="branch pushed 9 days ago")]


def test_cache_memoizes_claim_and_holds_lookups_within_one_cycle() -> None:
    calls: dict[str, int] = {"claim": 0, "holds": 0}

    def counting_claim_check(repo: str, issue: int) -> dict[str, Any]:
        calls["claim"] += 1
        return {"available": True, "held": False, "agent": "", "session": "", "reason": ""}

    def counting_holds_fetch(repo: str) -> str | None:
        calls["holds"] += 1
        return None

    cache = _cache(claim_check=counting_claim_check, holds_fetch=counting_holds_fetch)
    is_eligible("Runner_Dashboard", 1234, "claude-a", cache=cache)
    is_eligible("Runner_Dashboard", 1234, "claude-b", cache=cache)
    assert calls == {"claim": 1, "holds": 1}


def test_is_eligible_performs_no_writes_or_fence_minting(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("is_eligible must never write")

    monkeypatch.setattr(coordination_claims, "claim", fail)
    monkeypatch.setattr(coordination_claims, "release", fail)

    cache = _cache(claim_check=_claim_check(True, False))
    verdict = is_eligible("Runner_Dashboard", 1234, "claude-a", cache=cache)
    assert verdict.allowed is True


def test_default_cache_claim_check_is_coordination_claims_check(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_check(repo: str, issue: int) -> dict[str, Any]:
        captured["repo"], captured["issue"] = repo, issue
        return {"available": True, "held": False, "agent": "", "session": "", "reason": ""}

    monkeypatch.setattr(coordination_claims, "check", fake_check)
    verdict = is_eligible("Runner_Dashboard", 42, "claude-a")
    assert verdict.allowed is True
    assert captured == {"repo": "Runner_Dashboard", "issue": 42}


def test_default_cache_holds_fetch_reports_no_hold() -> None:
    """A cache with no ``holds_fetch`` override: the module itself knows nothing about
    ``staff.holds``, so an uninstrumented holds check reports no hold rather than
    guessing. Callers (launch_guard) are expected to supply a real holds_fetch."""
    cache = _cache(claim_check=_claim_check(True, False))
    verdict = is_eligible("Runner_Dashboard", 42, "claude-a", cache=cache)
    assert verdict.allowed is True
