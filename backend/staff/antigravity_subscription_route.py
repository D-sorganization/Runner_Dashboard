"""Fail-closed Antigravity subscription *route* evidence for strict panels (#4239).

``antigravity_oauth_session_alive`` proves the CLI can list models, not that the
effective execution path is subscription-backed (API keys, custom endpoints, or
stale OAuth without jetski markers must not satisfy ``require_verified_subscription``).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from staff.cli_oauth_session import (
    _has_refresh_token,
    antigravity_oauth_session_alive,
    oauth_expired_in_file,
    resolve_antigravity_home,
)

log = logging.getLogger(__name__)

_API_KEY_ENV_OVERRIDES: tuple[str, ...] = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTIGRAVITY_API_KEY",
)


@dataclass(frozen=True)
class AntigravityRouteSnapshot:
    """Local, non-secret route evidence; never holds credentials or account ids."""

    oauth_session_ok: bool
    models_listing_ok: bool
    has_subscription_marker: bool
    pay_per_use_route: bool
    custom_endpoint_without_marker: bool


@dataclass(frozen=True)
class AntigravityRouteVerdict:
    ok: bool
    reason: str = ""


def _read_settings_dict(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _settings_indicates_api_key(home: Path) -> bool:
    if any(os.environ.get(name, "").strip() for name in _API_KEY_ENV_OVERRIDES):
        return True
    for rel in ("settings.json", "antigravity-cli/settings.json"):
        data = _read_settings_dict(home / rel)
        if not data:
            continue
        for key in ("apiKey", "api_key"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return True
    return False


def _custom_endpoint_configured(home: Path) -> bool:
    if os.environ.get("ANTIGRAVITY_API_BASE", "").strip():
        return True
    for rel in ("settings.json", "antigravity-cli/settings.json"):
        data = _read_settings_dict(home / rel)
        if not data:
            continue
        for key in ("baseUrl", "base_url", "apiBase", "api_base"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return True
    return False


def _has_subscription_marker(home: Path) -> bool:
    jetski = home / "antigravity-cli" / "jetski_state.pbtxt"
    oauth = home / "oauth_creds.json"
    if jetski.is_file():
        return True
    if oauth.is_file() and not oauth_expired_in_file(oauth):
        return True
    if oauth.is_file() and _has_refresh_token(oauth):
        return True
    return False


def _oauth_session_files_ok(home: Path) -> bool:
    oauth_file = home / "oauth_creds.json"
    jetski_file = home / "antigravity-cli" / "jetski_state.pbtxt"
    login_markers = (
        home / "settings.json",
        home / "antigravity-cli" / "installation_id",
        jetski_file,
    )
    if not any(p.is_file() for p in login_markers) and not oauth_file.is_file():
        return False
    if oauth_expired_in_file(oauth_file) and not jetski_file.is_file() and not _has_refresh_token(oauth_file):
        return False
    return True


def collect_antigravity_route_snapshot(*, home: Path | None = None) -> AntigravityRouteSnapshot:
    """Gather bounded local evidence without logging secrets."""
    resolved = home if home is not None else resolve_antigravity_home()
    pay_per_use = _settings_indicates_api_key(resolved)
    has_marker = _has_subscription_marker(resolved)
    custom = _custom_endpoint_configured(resolved)
    oauth_ok = _oauth_session_files_ok(resolved)
    models_ok, _detail = antigravity_oauth_session_alive()
    return AntigravityRouteSnapshot(
        oauth_session_ok=oauth_ok,
        models_listing_ok=models_ok,
        has_subscription_marker=has_marker,
        pay_per_use_route=pay_per_use,
        custom_endpoint_without_marker=custom,
    )


def verify_antigravity_subscription_route(snap: AntigravityRouteSnapshot) -> AntigravityRouteVerdict:
    """Post: never returns credential material in ``reason``."""
    if snap.pay_per_use_route:
        return AntigravityRouteVerdict(False, "api_key_route_not_subscription")
    if snap.custom_endpoint_without_marker:
        return AntigravityRouteVerdict(False, "ambiguous_endpoint")
    if not snap.has_subscription_marker:
        return AntigravityRouteVerdict(False, "subscription_route_unverified")
    if not snap.oauth_session_ok or not snap.models_listing_ok:
        return AntigravityRouteVerdict(False, "subscription_session_not_live")
    return AntigravityRouteVerdict(True, "")


def verify_antigravity_subscription_route_for_panel() -> AntigravityRouteVerdict:
    return verify_antigravity_subscription_route(collect_antigravity_route_snapshot())


__all__ = [
    "AntigravityRouteSnapshot",
    "AntigravityRouteVerdict",
    "collect_antigravity_route_snapshot",
    "verify_antigravity_subscription_route",
    "verify_antigravity_subscription_route_for_panel",
]
