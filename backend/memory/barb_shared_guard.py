"""Barb shared memory guard (#3936 slice 0).

Marge-owned personal data (Gmail, ``+marge`` tag, Dropbox, Monarch) is classified from
sender/scope provenance and refused before persistence. All shared-memory text also passes the
rd-barb hold lint so locked holds cannot be lifted or auto-approved through memory writes or reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from memory.models import MemoryEntry
from staff.rd_barb_policy import HoldLint, LockedPolicyError, load_locked_policy

SHARED_DIGEST_MAX_CHARS = 400
_MARGE_CONNECTORS = frozenset({"gmail", "google-mail", "google_mail", "dropbox", "monarch"})
_MARGE_SCOPE_TOKENS = frozenset({"marge", "persona:marge", "role:marge", "scope:marge", "+marge"})
_MARGE_SENDER_TOKENS = frozenset({"marge", "persona:marge", "role:marge", "rd-marge", "rd_barb"})
_PROVENANCE_KEYS = (
    "sender",
    "from",
    "origin",
    "origin_sender",
    "scope",
    "scopes",
    "source_scope",
    "data_scope",
    "channel",
    "connector",
    "source_connector",
    "source",
)
_TAG_PREFIXES = ("scope:", "persona:", "sender:", "connector:", "source:", "from:", "tag:")


@dataclass(frozen=True)
class MemoryWriteProvenance:
    source_product: str
    tags: tuple[str, ...]
    body: dict[str, Any]
    author_principal: str = ""
    reported_origin_principal: str | None = None


def _norm(token: str) -> str:
    return token.strip().lower()


def _token_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [_norm(value)] if value.strip() else []
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_norm(str(v)) for v in value if isinstance(v, (str, int, float)) and str(v).strip()]
    return []


def _tag_tokens(tags: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for raw in tags:
        t = _norm(raw)
        if not t:
            continue
        out.append(t)
        if "+marge" in t:
            out.append("+marge")
        for prefix in _TAG_PREFIXES:
            if t.startswith(prefix):
                out.append(t.removeprefix(prefix))
    return out


def _connector_from_source_product(source_product: str) -> str | None:
    sp = _norm(source_product)
    if sp in _MARGE_CONNECTORS:
        return sp
    for name in _MARGE_CONNECTORS:
        if sp == name or sp.endswith(f"-{name}") or sp.startswith(f"{name}-"):
            return name
    return None


def marge_exclusion_reason(prov: MemoryWriteProvenance) -> str | None:
    """Return a refusal reason when provenance marks Marge-owned personal data."""
    tags = _tag_tokens(prov.tags)
    if any("+marge" in t or t in _MARGE_SCOPE_TOKENS for t in tags):
        return "marge_personal_excluded: +marge tag or scope"
    connector = _connector_from_source_product(prov.source_product)
    if connector is not None:
        return f"marge_personal_excluded: connector source_product {connector!r}"
    for tag in tags:
        for prefix in ("connector:", "source:"):
            if tag.startswith(prefix) and tag.removeprefix(prefix) in _MARGE_CONNECTORS:
                return f"marge_personal_excluded: tag marks connector {tag!r}"
    principals = [_norm(prov.author_principal), _norm(prov.reported_origin_principal or "")]
    principals += [p.removeprefix("principal:").removeprefix("agent:") for p in principals if p]
    if any(p in _MARGE_SENDER_TOKENS for p in principals if p):
        return "marge_personal_excluded: sender principal"
    for key in _PROVENANCE_KEYS:
        for token in _token_list(prov.body.get(key)):
            if token in _MARGE_SCOPE_TOKENS or token in _MARGE_SENDER_TOKENS:
                return f"marge_personal_excluded: body {key}={token!r}"
            if token in _MARGE_CONNECTORS:
                return f"marge_personal_excluded: body {key} marks connector {token!r}"
            if "+marge" in token:
                return "marge_personal_excluded: body scope carries +marge"
    return None


@lru_cache(maxsize=1)
def _hold_lint() -> HoldLint:
    return HoldLint(load_locked_policy())


def _hold_lint_safe() -> HoldLint | None:
    try:
        return _hold_lint()
    except LockedPolicyError:
        return None


def hold_lint_reason(*chunks: str) -> str | None:
    lint = _hold_lint_safe()
    if lint is None:
        return "memory_hold_lint: locked policy unavailable"
    haystack = " ".join(c for c in chunks if c and c.strip())
    if not haystack.strip():
        return None
    reason = lint.violation(haystack)
    return reason or None


def _collect_text(prov: MemoryWriteProvenance) -> list[str]:
    parts = [prov.source_product, prov.author_principal, prov.reported_origin_principal or ""]
    parts.extend(prov.tags)
    for value in prov.body.values():
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(v) for v in value if isinstance(v, str))
    return parts


def refuse_shared_memory_write(
    prov: MemoryWriteProvenance,
    *,
    entry_type: str,
    digest_summary: str | None = None,
) -> str | None:
    """Return a refusal reason before persisting shared memory, or ``None`` when allowed."""
    if entry_type == "session_digest" and digest_summary is not None:
        if len(digest_summary) > SHARED_DIGEST_MAX_CHARS:
            return f"shared_digest_too_large: max {SHARED_DIGEST_MAX_CHARS} chars"
    if reason := marge_exclusion_reason(prov):
        return reason
    if reason := hold_lint_reason(*_collect_text(prov)):
        return reason
    return None


def _entry_provenance(entry: MemoryEntry) -> MemoryWriteProvenance:
    return MemoryWriteProvenance(
        source_product=entry.source_product,
        tags=tuple(entry.tags),
        body=entry.body if isinstance(entry.body, dict) else {},
        author_principal=entry.author_principal,
        reported_origin_principal=str(entry.body.get("reported_origin_principal") or "")
        if isinstance(entry.body, dict)
        else None,
    )


def entry_allowed_in_shared_memory(entry: MemoryEntry) -> bool:
    """False when an entry must not be served from shared memory (read-time hold check)."""
    prov = _entry_provenance(entry)
    if marge_exclusion_reason(prov):
        return False
    if hold_lint_reason(*_collect_text(prov)):
        return False
    return True
