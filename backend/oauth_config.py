"""Fail-closed production GitHub OAuth configuration.

The browser OAuth credential is distinct from the GitHub App credential used
for backend API calls.  This module is deliberately pure and reads the process
environment only when :meth:`OAuthConfig.from_env` is called, so tests and
operators cannot observe stale import-time configuration.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TypedDict
from urllib.parse import urlencode

EXPECTED_PUBLIC_ORIGIN = "https://oglaptop.tail2bbcc7.ts.net"
EXPECTED_CALLBACK_URL = f"{EXPECTED_PUBLIC_ORIGIN}/api/auth/callback"
EXPECTED_GITHUB_ORG = "D-sorganization"
OAUTH_SCOPE = "read:user"

_MIN_CLIENT_ID_LENGTH = 8
_MIN_CLIENT_SECRET_LENGTH = 32
_MIN_SESSION_SECRET_LENGTH = 32
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_DEVELOPMENT_AUTH_FLAGS = ("DASHBOARD_DEV_LOGIN", "DASHBOARD_LOOPBACK_AUTH")


class OAuthDiagnostic(TypedDict):
    """Redacted readiness payload safe for health responses and logs."""

    ready: bool
    status: str
    reason: str


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

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> OAuthConfig:
        """Build a fresh snapshot from an explicit mapping or ``os.environ``."""
        source = os.environ if environ is None else environ
        return cls(
            client_id=source.get("GITHUB_CLIENT_ID", ""),
            client_secret=source.get("GITHUB_CLIENT_SECRET", ""),
            github_org=source.get("GITHUB_ORG", source.get("REQUIRED_GITHUB_ORG", "")),
            public_origin=source.get("DASHBOARD_PUBLIC_ORIGIN", ""),
            callback_url=source.get("GITHUB_OAUTH_CALLBACK_URL", ""),
            session_secret=source.get("SESSION_SECRET", ""),
            tls_enabled=_flag_enabled(source.get("DASHBOARD_TLS", "")),
            development_auth_present=any(name in source for name in _DEVELOPMENT_AUTH_FLAGS),
            dev_login_enabled=_flag_enabled(source.get("DASHBOARD_DEV_LOGIN", "")),
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
        if self.public_origin != EXPECTED_PUBLIC_ORIGIN:
            reasons.append("public_origin_mismatch")
        if self.callback_url != EXPECTED_CALLBACK_URL:
            reasons.append("callback_url_mismatch")
        if not _credential_is_valid(self.session_secret, _MIN_SESSION_SECRET_LENGTH):
            reasons.append("session_secret_invalid")
        if not self.tls_enabled:
            reasons.append("tls_required")
        if self.development_auth_present:
            reasons.append("development_auth_must_be_unset")
        if reasons:
            return {"ready": False, "status": "blocked", "reason": ";".join(reasons)}
        return {"ready": True, "status": "ready", "reason": "configured"}

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
