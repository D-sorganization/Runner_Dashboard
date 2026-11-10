"""RD#4239: Antigravity subscription route verification for strict panels."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from staff import antigravity_subscription_route as route


@pytest.mark.unit
def test_model_listing_alone_is_insufficient_for_subscription_proof() -> None:
    snap = route.AntigravityRouteSnapshot(
        oauth_session_ok=True,
        models_listing_ok=True,
        has_subscription_marker=False,
        pay_per_use_route=False,
        custom_endpoint_without_marker=False,
    )
    verdict = route.verify_antigravity_subscription_route(snap)
    assert verdict.ok is False
    assert verdict.reason == "subscription_route_unverified"


@pytest.mark.unit
@pytest.mark.parametrize(
    "snap_kwargs,reason",
    [
        ({"pay_per_use_route": True}, "api_key_route_not_subscription"),
        ({"custom_endpoint_without_marker": True, "has_subscription_marker": True}, "ambiguous_endpoint"),
        ({"oauth_session_ok": False, "has_subscription_marker": True}, "subscription_session_not_live"),
        ({"models_listing_ok": False, "has_subscription_marker": True}, "subscription_session_not_live"),
    ],
)
def test_fail_closed_on_untrusted_routes(snap_kwargs: dict[str, bool], reason: str) -> None:
    base = dict(
        oauth_session_ok=True,
        models_listing_ok=True,
        has_subscription_marker=True,
        pay_per_use_route=False,
        custom_endpoint_without_marker=False,
    )
    base.update(snap_kwargs)
    verdict = route.verify_antigravity_subscription_route(route.AntigravityRouteSnapshot(**base))
    assert verdict.ok is False
    assert verdict.reason == reason


@pytest.mark.unit
def test_positive_disk_markers_with_failed_live_probe_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "agy"
    cli = home / "antigravity-cli"
    cli.mkdir(parents=True)
    (cli / "jetski_state.pbtxt").write_text("marker", encoding="utf-8")
    (home / "oauth_creds.json").write_text(
        json.dumps({"expiry": 1, "refresh_token": "fixture-refresh-token"}),  # pragma: allowlist secret
        encoding="utf-8",
    )
    monkeypatch.setattr(route, "resolve_antigravity_home", lambda: home)
    monkeypatch.setattr(route, "antigravity_oauth_session_alive", lambda: (False, "agy auth probe exit 1"))
    snap = route.collect_antigravity_route_snapshot()
    assert snap.has_subscription_marker is True
    assert snap.oauth_session_ok is True
    assert snap.models_listing_ok is False
    verdict = route.verify_antigravity_subscription_route(snap)
    assert verdict.ok is False
    assert verdict.reason == "subscription_session_not_live"


@pytest.mark.unit
def test_verified_subscription_route_when_markers_and_live_probe_present() -> None:
    snap = route.AntigravityRouteSnapshot(
        oauth_session_ok=True,
        models_listing_ok=True,
        has_subscription_marker=True,
        pay_per_use_route=False,
        custom_endpoint_without_marker=False,
    )
    verdict = route.verify_antigravity_subscription_route(snap)
    assert verdict.ok is True
    assert verdict.reason == ""


@pytest.mark.unit
def test_collect_snapshot_detects_api_key_settings_without_leaking_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "agy"
    cli = home / "antigravity-cli"
    cli.mkdir(parents=True)
    (cli / "jetski_state.pbtxt").write_text("marker", encoding="utf-8")
    fixture_key = "fixture-non-secret-placeholder"  # pragma: allowlist secret
    (home / "settings.json").write_text(json.dumps({"apiKey": fixture_key}), encoding="utf-8")
    monkeypatch.setattr(route, "resolve_antigravity_home", lambda: home)
    monkeypatch.setattr(route, "antigravity_oauth_session_alive", lambda: (True, ""))
    snap = route.collect_antigravity_route_snapshot()
    assert snap.pay_per_use_route is True
    assert snap.has_subscription_marker is True
    verdict = route.verify_antigravity_subscription_route(snap)
    assert verdict.ok is False
    assert verdict.reason == "api_key_route_not_subscription"
    assert fixture_key not in verdict.reason


@pytest.mark.unit
def test_panel_subscription_check_fails_antigravity_when_route_unverified(monkeypatch: pytest.MonkeyPatch) -> None:
    from staff import panel_subscription_policy as policy

    monkeypatch.delenv("STAFF_DISABLED_PROVIDERS", raising=False)
    monkeypatch.setattr(
        policy,
        "verify_antigravity_subscription_route_for_panel",
        lambda: route.AntigravityRouteVerdict(False, "subscription_route_unverified"),
    )
    check = policy.check_provider_subscription("antigravity")
    assert check.ok is False
    assert check.reason == "subscription_route_unverified"
