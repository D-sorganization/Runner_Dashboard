"""Fail-closed production GitHub OAuth configuration.

Browser sign-in uses the fleet GitHub App's user-to-server OAuth client
(``GITHUB_CLIENT_ID`` / ``GITHUB_CLIENT_SECRET`` from the App settings, not a
separate OAuth App). Installation JWT credentials remain hub-only for agents.
This module is deliberately pure and reads the process environment only when
:meth:`OAuthConfig.from_env` is called, so tests and operators cannot observe
stale import-time configuration.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TypedDict
from urllib.parse import urlencode

from fleet_autoconfig import (
    hub_public_origins_from_registry,
    resolve_hub_public_origin_from_registry,
)
from machine_registry import load_machine_registry

log = logging.getLogger("dashboard.oauth_config")

EXPECTED_GITHUB_ORG = "D-sorganization"
OAUTH_SCOPE = "read:user"
OAUTH_CALLBACK_PATH = "/api/auth/callback"
OAUTH_ALLOWED_ORIGINS_ENV = "DASHBOARD_OAUTH_ALLOWED_ORIGINS"

_MIN_CLIENT_ID_LENGTH = 8
_MIN_CLIENT_SECRET_LENGTH = 32
_MIN_SESSION_SECRET_LENGTH = 32
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_DEVELOPMENT_AUTH_FLAGS = ("DASHBOARD_DEV_LOGIN", "DASHBOARD_LOOPBACK_AUTH", "DASHBOARD_LOOPBACK_OPERATOR_WRITES")


class OAuthDiagnostic(TypedDict, total=False):
    """Redacted readiness payload safe for health responses and logs."""

    ready: bool
    status: str
    reason: str
    flow: str


class OAuthConfigurationError(RuntimeError):
    """Raised when a production OAuth operation is attempted while blocked."""

    def __init__(self, diagnostic: OAuthDiagnostic) -> None:
        super().__init__(diagnostic["reason"])
        self.diagnostic = diagnostic


def _credential_is_valid(value: str, minimum_length: int) -> bool:
    """Return whether a credential is non-placeholder-like and whitespace-free."""
    return len(value) >= minimum_length and value == value.strip() and not any(char.isspace() for char in value)


def _flag_enabled(value: str) -> bool:
    return value.strip().lower() in _TRUE_VALUES


def _parse_extra_allowed_origins(value: str) -> frozenset[str]:
    origins: set[str] = set()
    for item in value.split(","):
        origin = item.strip().rstrip("/")
        if origin:
            origins.add(origin)
    return frozenset(origins)


def allowed_public_origins(
    environ: Mapping[str, str],
    *,
    registry: Mapping[str, object] | None = None,
) -> frozenset[str]:
    """Return HTTPS origins permitted for ``DASHBOARD_PUBLIC_ORIGIN``."""
    if registry is None:
        try:
            registry = load_machine_registry()
        except Exception:
            log.warning("Could not load machine registry for OAuth origin allowlist", exc_info=True)
            registry = {"machines": []}
    origins = set(hub_public_origins_from_registry(registry))
    origins.update(_parse_extra_allowed_origins(environ.get(OAUTH_ALLOWED_ORIGINS_ENV, "")))
    return frozenset(origins)


def _default_hub_public_origin() -> str:
    try:
        return resolve_hub_public_origin_from_registry(load_machine_registry())
    except Exception:
        log.warning("Could not resolve default hub OAuth origin from registry", exc_info=True)
        return ""


def _expected_callback_for_origin(public_origin: str) -> str:
    return f"{public_origin.rstrip('/')}{OAUTH_CALLBACK_PATH}"


@dataclass(frozen=True)
class OAuthConfig:
    """Typed production OAuth configuration boundary.

    Invariant: raw credentials are excluded from ``repr`` and never returned by
    :meth:`diagnostic`.  Callers must invoke :meth:`require_ready` before using
    authorization or token-exchange data.
    """

    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    github_org: str = field(repr=False)
    public_origin: str = field(repr=False)
    callback_url: str = field(repr=False)
    session_secret: str = field(repr=False)
    tls_enabled: bool
    development_auth_present: bool
    dev_login_enabled: bool
    _environment: Mapping[str, str] = field(repr=False, compare=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> OAuthConfig:
        """Build a fresh snapshot from an explicit mapping or ``os.environ``."""
        source = os.environ if environ is None else environ
        public_origin = source.get("DASHBOARD_PUBLIC_ORIGIN", "").strip()
        callback_url = source.get("GITHUB_OAUTH_CALLBACK_URL", "").strip()
        if not public_origin:
            public_origin = _default_hub_public_origin()
        if not callback_url and public_origin:
            callback_url = _expected_callback_for_origin(public_origin)
        return cls(
            client_id=source.get("GITHUB_CLIENT_ID", ""),
            client_secret=source.get("GITHUB_CLIENT_SECRET", ""),
            github_org=source.get("GITHUB_ORG", source.get("REQUIRED_GITHUB_ORG", "")),
            public_origin=public_origin,
            callback_url=callback_url,
            session_secret=source.get("SESSION_SECRET", ""),
            tls_enabled=_flag_enabled(source.get("DASHBOARD_TLS", "")),
            development_auth_present=any(name in source for name in _DEVELOPMENT_AUTH_FLAGS),
            dev_login_enabled=_flag_enabled(source.get("DASHBOARD_DEV_LOGIN", "")),
            _environment=source,
        )

    @property
    def client_id_configured(self) -> bool:
        """Return whether any OAuth client ID is present, valid or not."""
        return bool(self.client_id)

    def diagnostic(self) -> OAuthDiagnostic:
        """Return only readiness, status, and non-sensitive reason codes."""
        reasons: list[str] = []
        if not _credential_is_valid(self.client_id, _MIN_CLIENT_ID_LENGTH):
            reasons.append("github_client_id_invalid")
        if not _credential_is_valid(self.client_secret, _MIN_CLIENT_SECRET_LENGTH):
            reasons.append("github_client_secret_invalid")
        if self.github_org != EXPECTED_GITHUB_ORG:
            reasons.append("github_org_mismatch")
        permitted = allowed_public_origins(self._environment)
        if not self.public_origin or self.public_origin not in permitted:
            reasons.append("public_origin_mismatch")
        if self.callback_url != _expected_callback_for_origin(self.public_origin):
            reasons.append("callback_url_mismatch")
        if not _credential_is_valid(self.session_secret, _MIN_SESSION_SECRET_LENGTH):
            reasons.append("session_secret_invalid")
        if not self.tls_enabled:
            reasons.append("tls_required")
        if self.development_auth_present:
            reasons.append("development_auth_must_be_unset")
        if reasons:
            return {
                "ready": False,
                "status": "blocked",
                "reason": ";".join(reasons),
                "flow": "github_app_user_to_server",
            }
        return {
            "ready": True,
            "status": "ready",
            "reason": "configured",
            "flow": "github_app_user_to_server",
        }

    def require_ready(self) -> None:
        """Raise a redacted error unless every production invariant holds."""
        diagnostic = self.diagnostic()
        if not diagnostic["ready"]:
            raise OAuthConfigurationError(diagnostic)

    def authorization_url(self, state: str) -> str:
        """Build the exact GitHub authorization URL after validating ``state``."""
        self.require_ready()
        if len(state) < 20 or any(char.isspace() for char in state):
            raise ValueError("OAuth state must be at least 20 whitespace-free characters")
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.callback_url,
                "state": state,
                "scope": OAUTH_SCOPE,
            }
        )
        return f"https://github.com/login/oauth/authorize?{query}"

    def token_exchange_data(self, code: str) -> dict[str, str]:
        """Return GitHub token-exchange data bound to the registered callback."""
        self.require_ready()
        if not code or code != code.strip() or any(char.isspace() for char in code):
            raise ValueError("OAuth code must be a non-empty whitespace-free value")
        return {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": self.callback_url,
        }
