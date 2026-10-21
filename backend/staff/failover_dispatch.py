"""Fleet-wide provider failover: chain resolution, cooldown skip, cross-machine pick (#2047)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from typing import Any

from staff.adapters import READ_ONLY_RUN_PROVIDERS, ProviderAdapter
from staff.provider_cooldown import (
    earliest_cooldown_reset,
    mark_provider_cooldown,
    provider_in_cooldown,
)
from staff.model_tiers import normalize_tier
from staff.provider_failover_config import FailoverStep, resolve_failover_chain
from staff import quota as quota_mod
from staff.plan import RunRequest
from staff.roles import RoleSpec
from staff.runner_ops import can_run_unattended, read_only_kwargs

log = logging.getLogger("dashboard.staff.failover_dispatch")

FAILOVER_FAILURE_CLASSES = frozenset({"provider_capacity", "quota_exhausted", "rate_limited"})
IMMEDIATE_FAILOVER_CLASSES = frozenset({"provider_capacity", "quota_exhausted"})


def is_failover_failure(failure_class: str) -> bool:
    return failure_class in FAILOVER_FAILURE_CLASSES


def parse_chain_log(raw: str) -> list[dict[str, str]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def append_chain_attempt(
    store: Any,
    run_id: str,
    *,
    provider: str,
    model: str | None,
    outcome: str,
    tier: str = "",
) -> list[dict[str, str]]:
    rec = store.get_run(run_id)
    existing = parse_chain_log(getattr(rec, "provider_chain", "") if rec else "")
    entry = {
        "provider": provider,
        "model": model or "",
        "tier": tier or "",
        "outcome": outcome,
    }
    chain = [*existing, entry]
    store.update_run(run_id, provider_chain=json.dumps(chain))
    return chain


def _usable_provider(
    adapters: Mapping[str, ProviderAdapter],
    role: RoleSpec,
    pid: str,
) -> bool:
    if role.code_read_only and pid not in READ_ONLY_RUN_PROVIDERS:
        return False
    return can_run_unattended(adapters, pid) and adapters[pid].installed()


def pick_launch_step(
    chain: tuple[FailoverStep, ...],
    adapters: Mapping[str, ProviderAdapter],
    role: RoleSpec,
    *,
    skip_cooling: bool = True,
) -> FailoverStep | None:
    """First chain step that can run on this node (optionally skipping cooling providers)."""
    for step in chain:
        if skip_cooling and provider_in_cooldown(step.provider):
            continue
        if _usable_provider(adapters, role, step.provider):
            return step
    return None


def pick_machine_for_chain(
    board: dict[str, Any],
    chain: tuple[FailoverStep, ...],
    local_name: str,
    *,
    machine_cooldowns: dict[str, list[dict[str, Any]]] | None = None,
) -> str:
    """Choose an online machine with a healthy chain provider; least loaded wins."""
    cooling = machine_cooldowns or {}
    best: tuple[int, int, str] | None = None
    for name, node in (board.get("machines") or {}).items():
        if node.get("status") != "online":
            continue
        installed = node.get("providers") or {}
        cooled = {row["provider"] for row in cooling.get(name, node.get("providers_cooldown") or [])}
        pick_idx: int | None = None
        for idx, step in enumerate(chain):
            if not installed.get(step.provider, False):
                continue
            if step.provider in cooled:
                continue
            pick_idx = idx
            break
        if pick_idx is None:
            continue
        load = len(node.get("running", [])) + len(node.get("queued", []))
        rank = (pick_idx, load, 0 if name == local_name else 1, name)
        if best is None or rank < best:
            best = rank
    return best[3] if best else local_name
def resolve_provider_and_model(
    adapters: Mapping[str, ProviderAdapter],
    machine: str,
    req: RunRequest,
    role: RoleSpec,
) -> tuple[str, str | None, str]:
    """Pick provider and optional chain model for a staff run (#2047)."""
    import provider_switch as provider_switch_mod

    if role.code_read_only and req.provider and req.provider not in READ_ONLY_RUN_PROVIDERS:
        raise ValueError(
            f"role '{role.name}' runs code-read-only; provider {req.provider!r} has no read-only unattended mode"
        )
    ceiling = quota_mod.ceiling_percent(role.budget_max_window_percent)
    def _headroom(pid: str, lim: float | None) -> bool:
        return quota_mod.headroom(pid, lim if lim is not None else quota_mod.ceiling_percent(None))[0]

    if req.provider:
        provider = req.provider
    else:
        chain = resolve_failover_chain(role, requested_provider=None)
        if pick_launch_step(chain, adapters, role) is None and chain:
            raise ValueError(
                f"all providers in the failover chain are cooling down on {machine}; "
                "retry after the earliest reset in /api/staff/summary providers_cooldown"
            )
        provider, model, tier = select_provider_from_chain(
            adapters,
            role,
            req=req,
            requested_provider=None,
            ceiling=ceiling,
            quota_headroom=_headroom,
        )
        return provider, model, tier
    if provider not in adapters:
        raise ValueError(f"unknown provider '{provider}'")
    if req.provider and req.provider not in role.providers and role.name != "ad-hoc":
        allowed = ", ".join(role.providers)
        raise ValueError(f"provider '{req.provider}' is not allowed for role '{role.name}' (allowed: {allowed})")
    if provider_switch_mod.is_disabled(provider):
        raise ValueError(f"provider '{provider}' is {provider_switch_mod.DISABLED_DETAIL}")
    if not getattr(adapters[provider], "unattended", True):
        raise ValueError(
            f"provider '{provider}' is chat-only: it cannot run unattended without bypassing permissions"
        )
    from staff.failover_model import resolve_launch_model, role_base_tier

    model, tier = resolve_launch_model(
        req=req,
        role=role,
        provider=provider,
        adapter=adapters[provider],
        step=None,
        active_tier=role_base_tier(role),
    )
    return provider, model, tier


def select_provider_from_chain(
    adapters: Mapping[str, ProviderAdapter],
    role: RoleSpec,
    *,
    req: RunRequest | None,
    requested_provider: str | None,
    ceiling: float | None,
    quota_headroom: Callable[[str, float | None], bool],
) -> tuple[str, str | None, str]:
    """Resolve provider + model + tier for launch using failover chain and cooldown skip."""
    from staff.failover_model import resolve_launch_model, role_base_tier

    chain = resolve_failover_chain(role, requested_provider=requested_provider)
    step = pick_launch_step(chain, adapters, role)
    if step is None:
        step = chain[0] if chain else FailoverStep(role.providers[0], role.model)
    provider = step.provider
    if not quota_headroom(provider, ceiling):
        for alt in chain:
            if alt.provider != provider and quota_headroom(alt.provider, ceiling):
                if not provider_in_cooldown(alt.provider) and _usable_provider(adapters, role, alt.provider):
                    step = alt
                    provider = alt.provider
                    break
    adapter = adapters[provider]
    model, tier = resolve_launch_model(
        req=req,
        role=role,
        provider=provider,
        adapter=adapter,
        step=step,
        active_tier=role_base_tier(role),
    )
    return provider, model, tier
