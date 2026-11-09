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

import json
import os
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

import provider_switch
from security import safe_subprocess_env
from staff.cli_oauth_session import provider_oauth_session_alive, resolve_claude_config_dir
from staff.panel_models import MODERATOR_NAME, PanelCreateRequest

_SUBSCRIPTION_OVERRIDES: dict[str, tuple[str, ...]] = {
    "claude": (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_CUSTOM_HEADERS",
        "CLAUDE_CODE_OAUTH_TOKEN",
    ),
    "codex": ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE", "CODEX_API_KEY"),
}


def _codex_subscription_auth() -> bool:
    """Only an explicitly ChatGPT-authenticated Codex session qualifies."""
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        data = json.loads((home / "auth.json").read_text(encoding="utf-8"))
        config_path = home / "config.toml"
        config = tomllib.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    except (OSError, UnicodeError, ValueError, tomllib.TOMLDecodeError):
        return False
    providers = config.get("model_providers", {})
    tokens = data.get("tokens") if isinstance(data, dict) else None
    valid_config = (
        isinstance(data, dict)
        and data.get("auth_mode") == "chatgpt"
        and isinstance(tokens, dict)
        and isinstance(tokens.get("access_token"), str)
        and bool(tokens["access_token"].strip())
        and config.get("model_provider", "openai") == "openai"
        and not config.get("profile")
        and isinstance(providers, dict)
        and "openai" not in providers
    )
    if not valid_config:
        return False
    try:
        status = subprocess.run(
            ["codex", "login", "status"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            env=safe_subprocess_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return status.returncode == 0 and "logged in using chatgpt" in (status.stdout + status.stderr).lower()


def _claude_subscription_config() -> bool:
    """The probed Claude profile must be the profile used by the child process."""
    effective = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()
    settings_path = effective / "settings.json"
    try:
        if effective.resolve() != resolve_claude_config_dir().resolve():
            return False
        settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    except (OSError, UnicodeError, ValueError):
        return False
    if not isinstance(settings, dict) or settings.get("apiKeyHelper"):
        return False
    settings_env = settings.get("env", {})
    return isinstance(settings_env, dict) and not any(
        settings_env.get(name) for name in _SUBSCRIPTION_OVERRIDES["claude"]
    )


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
    if provider not in _SUBSCRIPTION_OVERRIDES:
        return SubscriptionCheck(provider, False, "provider has no verified subscription authentication path")
    if any(os.environ.get(name, "").strip() for name in _SUBSCRIPTION_OVERRIDES[provider]):
        return SubscriptionCheck(provider, False, "provider has an unverified credential or endpoint override")
    if provider == "codex" and not _codex_subscription_auth():
        return SubscriptionCheck(provider, False, "Codex ChatGPT subscription authentication not verified")
    if provider == "claude" and not _claude_subscription_config():
        return SubscriptionCheck(provider, False, "Claude subscription profile or settings not verified")
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
