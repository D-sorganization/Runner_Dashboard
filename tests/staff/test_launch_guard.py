"""Launch guard for cloud/agy/gemini dispatch (#3962 S1): refuse-with-reason, fail-closed."""

from __future__ import annotations

from typing import Any

import pytest
from staff.holds import Hold
from staff.launch_guard import LaunchGuardVerdict, check_launch_allowed

pytestmark = pytest.mark.unit


def _claim_check(available: bool, held: bool | None, reason: str = "") -> Any:
    def fn(repo: str, issue: int) -> dict[str, Any]:
        return {"available": available, "held": held, "agent": "", "session": "", "reason": reason}

    return fn


def test_allows_when_no_hold_and_claim_free() -> None:
    verdict = check_launch_allowed(
        "Runner_Dashboard", 1234, holds=[], claim_check=_claim_check(True, False)
    )
    assert verdict == LaunchGuardVerdict(allowed=True, reason="eligible")


def test_refuses_when_repo_held_by_a_schedule_hold() -> None:
    hold = Hold(id="h1", text="GM drain mode", applies_to=["repo:Runner_Dashboard"], kind="schedule")
    verdict = check_launch_allowed(
        "Runner_Dashboard", 1234, holds=[hold], claim_check=_claim_check(True, False)
    )
    assert verdict.allowed is False
    assert "held" in verdict.reason.lower()
    assert "GM drain mode" in verdict.reason


def test_guardrail_hold_never_blocks_launch() -> None:
    # Guardrail-kind holds (seeded from role YAML) never block scheduling (#1726) —
    # the launch guard follows the same existing HoldsList.blocking() convention.
    hold = Hold(id="h2", text="never deploy on Friday", applies_to=["*"], kind="guardrail")
    verdict = check_launch_allowed(
        "Runner_Dashboard", 1234, holds=[hold], claim_check=_claim_check(True, False)
    )
    assert verdict.allowed is True


def test_inactive_hold_never_blocks_launch() -> None:
    hold = Hold(id="h3", text="old hold", applies_to=["*"], kind="schedule", active=False)
    verdict = check_launch_allowed(
        "Runner_Dashboard", 1234, holds=[hold], claim_check=_claim_check(True, False)
    )
    assert verdict.allowed is True


def test_hold_targeting_a_different_repo_does_not_block() -> None:
    hold = Hold(id="h4", text="GM drain mode", applies_to=["repo:Gasification_Model"], kind="schedule")
    verdict = check_launch_allowed(
        "Runner_Dashboard", 1234, holds=[hold], claim_check=_claim_check(True, False)
    )
    assert verdict.allowed is True


def test_refuses_when_issue_already_claimed_or_has_open_pr() -> None:
    verdict = check_launch_allowed(
        "Runner_Dashboard",
        1234,
        holds=[],
        claim_check=_claim_check(True, True, reason="held by claude-b, open PR #999"),
    )
    assert verdict.allowed is False
    assert "claimed" in verdict.reason.lower() or "held" in verdict.reason.lower()
    assert "open PR #999" in verdict.reason


def test_fails_closed_when_claim_check_cannot_answer() -> None:
    verdict = check_launch_allowed(
        "Runner_Dashboard",
        1234,
        holds=[],
        claim_check=_claim_check(False, None, reason="check_agent_claim failed open (error:Timeout)"),
    )
    assert verdict.allowed is False
    assert "unavailable" in verdict.reason.lower() or "unknown" in verdict.reason.lower()


def test_fails_closed_when_claim_check_raises() -> None:
    def raising_check(repo: str, issue: int) -> dict[str, Any]:
        raise RuntimeError("boom")

    verdict = check_launch_allowed("Runner_Dashboard", 1234, holds=[], claim_check=raising_check)
    assert verdict.allowed is False
    assert "boom" in verdict.reason


def test_default_claim_check_is_coordination_claims_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no override, the guard reuses the existing coordination.claims.check (no second claim store)."""
    from staff import launch_guard

    captured: dict[str, Any] = {}

    def fake_check(repo: str, issue: int) -> dict[str, Any]:
        captured["repo"], captured["issue"] = repo, issue
        return {"available": True, "held": False, "agent": "", "session": "", "reason": ""}

    monkeypatch.setattr(launch_guard.coordination_claims, "check", fake_check)
    verdict = launch_guard.check_launch_allowed("Runner_Dashboard", 42, holds=[])
    assert verdict.allowed is True
    assert captured == {"repo": "Runner_Dashboard", "issue": 42}


def test_default_holds_source_is_holdslist_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no override, the guard reuses the existing HoldsList().blocking('*', repo) convention."""
    from staff import launch_guard

    hold = Hold(id="h5", text="repo drained", applies_to=["*"], kind="schedule")

    class FakeHoldsList:
        def blocking(self, role: str, repo: str = "") -> Hold | None:
            assert role == "*"
            assert repo == "Runner_Dashboard"
            return hold

    monkeypatch.setattr(launch_guard, "HoldsList", FakeHoldsList)
    verdict = launch_guard.check_launch_allowed(
        "Runner_Dashboard", 42, claim_check=_claim_check(True, False)
    )
    assert verdict.allowed is False
    assert "repo drained" in verdict.reason
