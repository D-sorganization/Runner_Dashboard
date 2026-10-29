"""Private-role context accessor (issue #3094, epic #3077 private-roles slice 1).

Private-role definitions live in the overlay (``overlay_contract.Overlay``),
never in core. This module is the single accessor for private host-local
context (LoD/DRY): callers ask for context by role id and template name and
never open the context-store files, read the host key, or inspect the
overlay's role list themselves.

Fail-closed: every missing or mismatched piece (role, requester, template,
provider, key, store, version) makes the caller refuse the task and returns a
:class:`PrivateContextUnavailable` degraded state instead of raising past the
caller or returning context as if nothing were wrong. Degraded-state reasons
are a small fixed, non-sensitive vocabulary; they never echo paths, keys, or
stored content.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import private_context_audit
from cryptography.fernet import Fernet, InvalidToken
from overlay_contract import Overlay, PrivateRole

CONTEXT_KEY_PATH_ENV = "RD_PRIVATE_CONTEXT_KEY_PATH"
_STORE_VERSION_FILE = ".store-schema-version"

Reason = str
_REASONS: frozenset[Reason] = frozenset(
    {
        "role-not-found",
        "not-owner",
        "template-not-allowlisted",
        "provider-not-approved",
        "key-missing",
        "store-missing",
        "version-skew",
        "decrypt-failed",
        "audit-write-failed",
    }
)


@dataclass(frozen=True)
class PrivateContextUnavailable:
    """Visible, non-sensitive fail-closed state. Never treat this as context."""

    role_id: str
    reason: Reason

    def __post_init__(self) -> None:
        if self.reason not in _REASONS:
            raise ValueError(f"unknown PrivateContextUnavailable reason: {self.reason}")


def find_role(overlay: Overlay | None, role_id: str) -> PrivateRole | None:
    """The only place that walks the overlay's private-role list."""
    return overlay.private_role(role_id) if overlay is not None else None


def provider_is_approved(role: PrivateRole, host: str, provider_cli: str) -> bool:
    return provider_cli in role.providers.get(host, ())


def _read_context_key(env: Mapping[str, str]) -> bytes | None:
    key_path = env.get(CONTEXT_KEY_PATH_ENV, "").strip()
    if not key_path:
        return None
    try:
        key = Path(key_path).expanduser().read_bytes()
    except OSError:
        return None
    return key or None


def _read_store_version(store_dir: Path) -> int | None:
    try:
        text = (store_dir / _STORE_VERSION_FILE).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return int(text.strip())
    except ValueError:
        return None


def read_private_context(
    *,
    overlay: Overlay | None,
    role_id: str,
    requesting_role_id: str,
    template_name: str,
    host: str,
    provider_cli: str,
    env: Mapping[str, str] | None = None,
    audit_store: private_context_audit.PrivateContextAuditStore | None = None,
) -> bytes | PrivateContextUnavailable:
    """Return decrypted context for an allowlisted template, or a degraded state.

    DbC preconditions (role ownership, template allowlist, provider approval,
    store/key presence, schema compatibility) are all checked here, inside
    the one accessor; no caller re-implements any of them (LoD).

    Every call writes exactly one metadata-only audit entry (issue #3096); a
    failed write fails closed, even on an otherwise-successful read.
    """
    result = _read_private_context_unaudited(
        overlay=overlay,
        role_id=role_id,
        requesting_role_id=requesting_role_id,
        template_name=template_name,
        host=host,
        provider_cli=provider_cli,
        env=env,
    )
    outcome = result.reason if isinstance(result, PrivateContextUnavailable) else "success"
    try:
        private_context_audit.record_access(
            role_id=role_id,
            template_name=template_name,
            provider_cli=provider_cli,
            host=host,
            outcome=outcome,
            store=audit_store,
        )
    except private_context_audit.PrivateContextAuditError:
        return PrivateContextUnavailable(role_id, "audit-write-failed")
    return result


def _read_private_context_unaudited(
    *,
    overlay: Overlay | None,
    role_id: str,
    requesting_role_id: str,
    template_name: str,
    host: str,
    provider_cli: str,
    env: Mapping[str, str] | None = None,
) -> bytes | PrivateContextUnavailable:
    env = os.environ if env is None else env
    role = find_role(overlay, role_id)
    if role is None:
        return PrivateContextUnavailable(role_id, "role-not-found")
    if requesting_role_id != role_id:
        return PrivateContextUnavailable(role_id, "not-owner")
    if template_name not in role.allowed_templates:
        return PrivateContextUnavailable(role_id, "template-not-allowlisted")
    if not provider_is_approved(role, host, provider_cli):
        return PrivateContextUnavailable(role_id, "provider-not-approved")
    key = _read_context_key(env)
    if key is None:
        return PrivateContextUnavailable(role_id, "key-missing")
    store_dir = Path(role.context_store_dir).expanduser()
    stored_version = _read_store_version(store_dir)
    if stored_version is None:
        return PrivateContextUnavailable(role_id, "store-missing")
    if stored_version != role.store_schema_version:
        return PrivateContextUnavailable(role_id, "version-skew")
    try:
        ciphertext = (store_dir / f"{template_name}.age").read_bytes()
    except OSError:
        return PrivateContextUnavailable(role_id, "store-missing")
    try:
        return Fernet(key).decrypt(ciphertext)
    except (InvalidToken, ValueError):
        return PrivateContextUnavailable(role_id, "decrypt-failed")


def is_unavailable(result: bytes | PrivateContextUnavailable) -> bool:
    return isinstance(result, PrivateContextUnavailable)
