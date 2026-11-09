"""Fail-closed subscription-only panel policy (#4223).

- A disabled provider or one with no live OAuth session fails the check closed, never a key check.
- ``verify_panel_subscriptions`` reports every expert and the moderator, naming only the failures.
"""

from __future__ import annotations

from typing import Any

import pytest
import staff.panel_subscription_policy as policy
from staff.panel_models import MODERATOR_NAME, PanelCreateRequest

EXPERTS = [
    {"name": "Ada", "perspective": "numerical methods", "provider": "claude"},
    {"name": "Brook", "perspective": "experimental physics", "provider": "codex"},
    {"name": "Cy", "perspective": "software architecture", "provider": "antigravity"},
]


def _request(**overrides: Any) -> PanelCreateRequest:
    data: dict[str, Any] = {"topic": "Use implicit stepping?", "experts": EXPERTS, "rounds": 1}
    data.update(overrides)
    return PanelCreateRequest.model_validate(data)


@pytest.mark.unit
def test_a_disabled_provider_fails_closed_without_checking_oauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STAFF_DISABLED_PROVIDERS", "codex")
    monkeypatch.setattr(policy, "provider_oauth_session_alive", lambda _p: (False, "should never be called"))
    check = policy.check_provider_subscription("codex")
    assert check.ok is False
    assert "disabled on this node" in check.reason


@pytest.mark.unit
def test_an_enabled_provider_with_no_live_session_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAFF_DISABLED_PROVIDERS", raising=False)
    monkeypatch.setattr(policy, "provider_oauth_session_alive", lambda _p: (False, "no Codex auth.json"))
    check = policy.check_provider_subscription("codex")
    assert check.ok is False and check.reason == "no Codex auth.json"


@pytest.mark.unit
def test_a_live_subscription_session_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAFF_DISABLED_PROVIDERS", raising=False)
    monkeypatch.setattr(policy, "provider_oauth_session_alive", lambda _p: (True, ""))
    check = policy.check_provider_subscription("claude")
    assert check.ok is True and check.reason == ""


@pytest.mark.unit
def test_panel_seats_lists_every_expert_then_the_moderator() -> None:
    seats = policy.panel_seats(_request(moderator_provider="antigravity"))
    assert seats == [("Ada", "claude"), ("Brook", "codex"), ("Cy", "antigravity"), (MODERATOR_NAME, "antigravity")]


@pytest.mark.unit
def test_verify_panel_subscriptions_names_only_the_failing_seats(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAFF_DISABLED_PROVIDERS", raising=False)
    from staff.antigravity_subscription_route import AntigravityRouteVerdict

    def fake_alive(provider: str) -> tuple[bool, str]:
        return (provider != "codex", "" if provider != "codex" else "no Codex auth.json")

    monkeypatch.setattr(policy, "provider_oauth_session_alive", fake_alive)
    monkeypatch.setattr(
        policy,
        "verify_antigravity_subscription_route_for_panel",
        lambda: AntigravityRouteVerdict(True, ""),
    )
    failures = policy.verify_panel_subscriptions(_request())
    assert failures == [{"seat": "Brook", "provider": "codex", "reason": "no Codex auth.json"}]


@pytest.mark.unit
def test_verify_panel_subscriptions_is_empty_when_every_seat_is_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAFF_DISABLED_PROVIDERS", raising=False)
    from staff.antigravity_subscription_route import AntigravityRouteVerdict

    monkeypatch.setattr(policy, "provider_oauth_session_alive", lambda _p: (True, ""))
    monkeypatch.setattr(
        policy,
        "verify_antigravity_subscription_route_for_panel",
        lambda: AntigravityRouteVerdict(True, ""),
    )
    assert policy.verify_panel_subscriptions(_request()) == []
