"""Read-only staff runs: provider substitution, chain diagnostics, authority alignment (#4452)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from staff.adapters import READ_ONLY_RUN_PROVIDERS, ProviderAdapter
from staff.plan import RunRequest
from staff.provider_cooldown import provider_in_cooldown
from staff.provider_failover_config import FailoverStep, resolve_failover_chain
from staff.runner_ops import can_run_unattended

from staff.roles import RoleSpec


def align_role_read_only_authority(role: RoleSpec, req: RunRequest) -> RoleSpec:
    """Force ``code_read_only`` when the authority forwarded a read-only run to a stale role copy."""
    if not req.authority_code_read_only or role.code_read_only:
        return role
    perms = {**dict(role.permissions), "code_read_only": True}
    return replace(role, permissions=perms)


def code_read_only_chain_warnings(data: dict[str, Any]) -> list[str]:
    """Non-fatal YAML warnings for ``code_read_only`` roles and their failover chain."""
    from staff.provider_failover_config import parse_role_failover_chain

    raw_perms = data.get("permissions")
    perms = raw_perms if isinstance(raw_perms, dict) else {}
    if perms.get("code_read_only") is not True:
        return []
    warnings: list[str] = []
    chain = parse_role_failover_chain(data)
    if not chain:
        providers = data.get("providers")
        if isinstance(providers, list) and any(p in READ_ONLY_RUN_PROVIDERS for p in providers):
            return warnings
        warnings.append("code_read_only role has no failover_chain providers configured")
        return warnings
    head = chain[0].provider
    if head not in READ_ONLY_RUN_PROVIDERS:
        warnings.append(f"code_read_only failover_chain[0] provider {head!r} has no read-only unattended mode")
    if not any(step.provider in READ_ONLY_RUN_PROVIDERS for step in chain):
        warnings.append("code_read_only role has no read-only-capable provider in failover_chain")
    return warnings


def read_only_substitution_skips(
    req: RunRequest,
    *,
    store: Any = None,
    gh_probe: Any = None,
) -> frozenset[str] | None:
    """Providers to skip when substituting for an explicit non-read-only request (cross-provider rule)."""
    if not req.pr or not req.repo:
        return None
    from staff.review import detect_author_provider  # noqa: PLC0415 — re-exported for tests

    author = detect_author_provider(
        repo=req.repo,
        pr_number=int(req.pr),
        branch=req.head_ref or "",
        store=store,
        gh_probe=gh_probe,
    )
    if not author:
        return None
    return frozenset({author})


def _skip_reason(
    adapters: Mapping[str, ProviderAdapter],
    role: RoleSpec,
    provider: str,
    *,
    machine: str,
    repo: str,
    skip_providers: frozenset[str] | None,
) -> str | None:
    from staff.failover_dispatch import _provider_auth_blocks, _provider_installed, _usable_provider
    from staff.role_provider_chains import filter_chain_for_repo

    if skip_providers and provider in skip_providers:
        return "cross-provider: same as PR author"
    filtered = filter_chain_for_repo((FailoverStep(provider),), repo)
    if not filtered:
        return "repo policy bars this provider"
    if role.code_read_only and provider not in READ_ONLY_RUN_PROVIDERS:
        return "no read-only unattended mode"
    if _provider_auth_blocks(provider, machine):
        return "auth expired or missing"
    if provider_in_cooldown(provider):
        return "provider cooling down"
    if not _provider_installed(adapters, provider):
        return "not installed on this node"
    if not can_run_unattended(adapters, provider):
        return "chat-only provider"
    if not _usable_provider(adapters, role, provider):
        return "unavailable"
    return None


def explain_read_only_chain_exhausted(
    adapters: Mapping[str, ProviderAdapter],
    role: RoleSpec,
    req: RunRequest,
    *,
    machine: str,
    skip_providers: frozenset[str] | None,
) -> str:
    repo = str(req.repo or "")
    chain = resolve_failover_chain(
        role,
        requested_provider=req.provider,
        repo=repo,
        skip_providers=skip_providers,
    )
    details: list[str] = []
    for step in chain:
        reason = _skip_reason(
            adapters,
            role,
            step.provider,
            machine=machine,
            repo=repo,
            skip_providers=skip_providers,
        )
        if reason is not None:
            details.append(f"{step.provider}: {reason}")
    body = "; ".join(details) if details else "no candidates in failover chain"
    return f"no read-only-capable provider available in failover chain for role '{role.name}'; {body}"


def role_read_only_config_warnings(spec: RoleSpec) -> list[str]:
    """Configuration warnings for one loaded :class:`RoleSpec` (health aggregate)."""
    if not spec.code_read_only:
        return []
    warnings: list[str] = []
    chain = spec.failover_chain
    if not chain:
        provs = tuple(p for p in spec.providers if p in READ_ONLY_RUN_PROVIDERS)
        if not provs:
            warnings.append("no read-only-capable provider in role providers list")
        return warnings
    if chain[0].provider not in READ_ONLY_RUN_PROVIDERS:
        warnings.append(f"failover_chain[0] provider {chain[0].provider!r} has no read-only unattended mode")
    if not any(step.provider in READ_ONLY_RUN_PROVIDERS for step in chain):
        warnings.append("no read-only-capable provider in failover_chain")
    return warnings


def reviewer_provider_available_on_board(board: dict[str, Any], role: RoleSpec) -> dict[str, bool]:
    """Per-node whether any read-only-capable chain provider is installed (fleet board snapshot)."""
    if not role.code_read_only:
        return {}
    targets = {s.provider for s in role.failover_chain if s.provider in READ_ONLY_RUN_PROVIDERS}
    if not targets:
        targets = {p for p in role.providers if p in READ_ONLY_RUN_PROVIDERS}
    out: dict[str, bool] = {}
    for name, node in (board.get("machines") or {}).items():
        if node.get("status") != "online":
            out[name] = False
            continue
        installed = node.get("providers") or {}
        out[name] = any(installed.get(pid) for pid in targets)
    return out
