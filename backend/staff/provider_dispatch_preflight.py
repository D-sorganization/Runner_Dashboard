"""Refuse staff dispatch when a provider OAuth seat is expired (#2170)."""

from __future__ import annotations

from dataclasses import dataclass

from agent_remediation.provider_registry import PROVIDER_REGISTRY, by_dashboard_id
from staff.classifier import FailureClassification, _login_command
from staff.cli_oauth_session import provider_oauth_session_alive


@dataclass(frozen=True)
class ProviderAuthRefusal:
    failure_class: str
    error: str
    remediation: str

    def to_http_detail(self) -> dict[str, str]:
        return {
            "code": "provider_auth_expired",
            "message": self.error,
            "remediation": self.remediation,
            "failure_class": self.failure_class,
        }


def _credential_id_for_provider(provider: str) -> str | None:
    entry = by_dashboard_id().get(provider)
    if entry is None:
        for row in PROVIDER_REGISTRY:
            if row.dashboard_id == provider:
                entry = row
                break
    if entry is None:
        return None
    return entry.effective_credential_id or None


def refusal_for_provider_auth(provider: str, machine: str) -> ProviderAuthRefusal | None:
    """Return a refusal when OAuth is required but the session is not alive.

    Pre: ``provider`` is the resolved staff provider id (e.g. ``claude``, ``antigravity``).
    Post: ``None`` when dispatch may proceed; otherwise a non-retryable ``auth_expired`` refusal.
    """
    cred_id = _credential_id_for_provider(provider) or provider
    alive, detail = provider_oauth_session_alive(cred_id)
    if alive:
        return None
    login_cmd = _login_command(provider)
    node = machine or "this node"
    return ProviderAuthRefusal(
        failure_class="auth_expired",
        error=f"Authentication expired for {provider} on {node}" + (f" ({detail})" if detail else ""),
        remediation=f"Run `{login_cmd}` on node {node}.",
    )
