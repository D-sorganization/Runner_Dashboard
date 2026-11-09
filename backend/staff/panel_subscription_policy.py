"""Opt-in fail-closed subscription-only policy for expert panels (#4223).

A panel normally tries each seat's provider through an ordered fallback chain
(``seat_fallback.py``), so a turn can silently land on a different provider or
model than the caller asked for. Under this policy (``PanelCreateRequest.
require_verified_subscription``) every expert and the moderator is pinned to
its own provider and model with no substitution, and that provider's
subscription authentication must be independently verified -- disabled
providers and pay-per-use credentials never count -- both before the panel
starts and again immediately before each turn.
"""

from __future__ import annotations

from dataclasses import dataclass

import provider_switch
from staff.antigravity_subscription_route import verify_antigravity_subscription_route_for_panel
from staff.cli_oauth_session import provider_oauth_session_alive
from staff.panel_models import MODERATOR_NAME, PanelCreateRequest


@dataclass(frozen=True)
class SubscriptionCheck:
    """The verdict for one provider: never includes credential material."""

    provider: str
    ok: bool
    reason: str = ""


def check_provider_subscription(provider: str) -> SubscriptionCheck:
    """Verify ``provider`` is enabled on this node and its subscription session is live.

    A provider switched off (#1597) or with no live OAuth session fails closed;
    this never consults or reports a pay-per-use API key.
    """
    if provider_switch.is_disabled(provider):
        return SubscriptionCheck(provider, False, provider_switch.DISABLED_DETAIL)
    if provider == "antigravity":
        route = verify_antigravity_subscription_route_for_panel()
        return SubscriptionCheck(provider, route.ok, route.reason)
    alive, detail = provider_oauth_session_alive(provider)
    return SubscriptionCheck(provider, alive, "" if alive else (detail or "subscription session not verified"))


def panel_seats(request: PanelCreateRequest) -> list[tuple[str, str]]:
    """``(seat name, provider)`` for every expert and the moderator, in speaking order."""
    seats = [(e.name, e.provider) for e in request.experts]
    seats.append((MODERATOR_NAME, request.moderator_provider))
    return seats


def verify_panel_subscriptions(request: PanelCreateRequest) -> list[dict[str, str]]:
    """The seats whose provider is not verified; empty when every seat may proceed.

    Each entry names the seat and provider and gives the failure reason, never credentials.
    """
    failures: list[dict[str, str]] = []
    for name, provider in panel_seats(request):
        check = check_provider_subscription(provider)
        if not check.ok:
            failures.append({"seat": name, "provider": provider, "reason": check.reason})
    return failures


__all__ = [
    "SubscriptionCheck",
    "check_provider_subscription",
    "panel_seats",
    "verify_panel_subscriptions",
]
