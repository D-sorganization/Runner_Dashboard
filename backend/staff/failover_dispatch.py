"""Fleet-wide provider failover: chain resolution, cooldown skip, cross-machine pick (#2047)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from typing import Any

from staff import quota as quota_mod
from staff.adapters import READ_ONLY_RUN_PROVIDERS, ProviderAdapter
from staff.plan import RunRequest
from staff.provider_cooldown import (
    earliest_cooldown_reset,
    mark_provider_cooldown,
    provider_in_cooldown,
)
from staff.provider_failover_config import FailoverStep, resolve_failover_chain
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
    return can_run_unattended(adapters, pid)


def _provider_auth_blocks(provider: str, machine: str) -> bool:
    from staff.provider_dispatch_preflight import refusal_for_provider_auth  # noqa: PLC0415

    return refusal_for_provider_auth(provider, machine) is not None


def pick_launch_step(
    chain: tuple[FailoverStep, ...],
    adapters: Mapping[str, ProviderAdapter],
    role: RoleSpec,
    *,
    skip_cooling: bool = True,
    machine: str = "local",
    auth_skipped: list[dict[str, str]] | None = None,
) -> FailoverStep | None:
    """First chain step that can run on this node (optionally skipping cooling providers)."""
    for step in chain:
        if skip_cooling and provider_in_cooldown(step.provider):
            continue
        if _provider_auth_blocks(step.provider, machine):
            if auth_skipped is not None:
                auth_skipped.append({"provider": step.provider, "outcome": "auth_expired"})
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
    best: tuple[int, int, int, str] | None = None
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


def tried_slots_for_root(store: Any, root_id: str) -> set[str]:
    from staff.failover_model import slot_key  # noqa: PLC0415

    root = store.get_run(root_id)
    tried: set[str] = set()
    if root is None:
        return tried
    chain = parse_chain_log(getattr(root, "provider_chain", ""))
    for entry in chain:
        tried.add(slot_key(entry.get("provider", ""), entry.get("tier", "")))
    for row in store.get_attempts(root_id):
        tried.add(slot_key(row.provider, getattr(row, "model_tier", "") or ""))
    return tried


def tried_providers_for_root(store: Any, root_id: str) -> set[str]:
    return {s.split(":", 1)[0] for s in tried_slots_for_root(store, root_id)}


def try_chain_failover(
    runner: Any,
    latest: Any,
    plan: Any,
    role: RoleSpec,
    guard: Any,
    *,
    retries_exhausted: bool,
) -> bool:
    """Schedule the next provider in the fleet failover chain when appropriate."""
    from staff.plan import RunPlan  # noqa: PLC0415
    from staff.retry import _claim, _provenance, _schedule, attempt_run_id  # noqa: PLC0415
    from staff.store import RunRecord  # noqa: PLC0415

    fc = latest.failure_class
    if not is_failover_failure(fc):
        return False
    immediate = fc in IMMEDIATE_FAILOVER_CLASSES
    if not immediate and not retries_exhausted and fc == "rate_limited":
        return False

    mark_provider_cooldown(plan.provider, fc, message=latest.error or "")
    root_id = latest.retry_of or latest.id
    from staff.failover_model import resolve_launch_model, role_base_tier, slot_key
    from staff.model_tiers import normalize_tier

    raw_tier = (getattr(latest, "model_tier", None) or "").strip().lower()
    active_tier = normalize_tier(raw_tier) if raw_tier in ("routine", "standard", "complex") else role_base_tier(role)
    append_chain_attempt(
        runner.store,
        root_id,
        provider=plan.provider,
        model=plan.model,
        outcome=fc,
        tier=active_tier,
    )

    chain = resolve_failover_chain(role, requested_provider=None)
    tried_slots = tried_slots_for_root(runner.store, root_id)
    next_step: FailoverStep | None = None
    next_tier = active_tier

    while next_step is None:
        for step in chain:
            key = slot_key(step.provider, next_tier)
            if key in tried_slots:
                continue
            if provider_in_cooldown(step.provider):
                continue
            if not _usable_provider(runner._adapters, role, step.provider):
                continue
            next_step = step
            break
        if next_step is not None:
            break
        if not getattr(role, "allow_tier_escalation", False):
            break
        break  # tier escalation wiring lands in slice 5b

    if next_step is None:
        _mark_providers_exhausted(runner.store, latest, root_id, plan)
        return True

    can_run, _ = guard.can_run(role)
    if not can_run:
        return False

    next_attempt = latest.attempt + 1
    attempt_id = attempt_run_id(root_id, next_attempt)
    next_branch = f"{plan.branch}-failover-{next_attempt}"
    target_model, resolved_tier = resolve_launch_model(
        req=None,
        role=role,
        provider=next_step.provider,
        adapter=runner._adapters[next_step.provider],
        step=next_step,
        active_tier=next_tier,
    )
    argv = runner._adapters[next_step.provider].build_command(
        plan.prompt, "<workdir>", target_model, **read_only_kwargs(role)
    )
    fallback_plan = RunPlan(
        role=plan.role,
        provider=next_step.provider,
        model=target_model,
        model_tier=resolved_tier,
        repo=plan.repo,
        target_kind=plan.target_kind,
        target_ref=plan.target_ref,
        operator_prompt=plan.operator_prompt,
        prompt=plan.prompt,
        argv=argv,
        branch=next_branch,
        lease_ritual=plan.lease_ritual,
        consolidation=plan.consolidation,
        focus=plan.focus,
        head_ref=plan.head_ref,
    )
    fallback_rec = RunRecord(
        id=attempt_id,
        role=plan.role,
        provider=next_step.provider,
        model=target_model,
        model_tier=resolved_tier,
        machine=runner.machine,
        repo=plan.repo,
        target_kind=plan.target_kind,
        target_ref=plan.target_ref,
        prompt=plan.prompt,
        requested_by=latest.requested_by,
        on_behalf_of=latest.on_behalf_of,
        branch=next_branch,
        strategy_mode=plan.strategy_mode,
        retry_of=root_id,
        attempt=next_attempt,
        max_attempts=latest.max_attempts + 1,
        fallback_provider=next_step.provider,
        **(_provenance(latest)),
    )
    if not _claim(runner.store, fallback_rec):
        return False
    runner.store.append_event(
        root_id,
        "failover",
        f"{fc} on {plan.provider}; chain failover to {next_step.provider}"
        + (f" ({target_model})" if target_model else ""),
    )
    _schedule(runner, fallback_rec, fallback_plan, 0.0)
    return True


def _mark_providers_exhausted(store: Any, latest: Any, root_id: str, plan: Any) -> None:
    reset_at = earliest_cooldown_reset() or ""
    msg = f"All providers in failover chain exhausted; earliest reset {reset_at or 'unknown'}"
    store.update_run(
        latest.id,
        status="blocked",
        failure_class="providers_exhausted",
        retryable=False,
        remediation=msg,
        error=msg,
        next_attempt_at=reset_at or None,
    )
    if root_id != latest.id:
        store.update_run(
            root_id,
            status="blocked",
            failure_class="providers_exhausted",
            retryable=False,
            remediation=msg,
            error=msg,
            next_attempt_at=reset_at or None,
        )
    store.append_event(root_id, "providers_exhausted", msg)


def run_completion_summary(
    store: Any,
    rec: Any,
    result_line: str,
    *,
    provider: str,
    model: str | None,
) -> str:
    """Merge STAFF_RESULT text with failover chain brief when present (#2047)."""
    from staff.adapter_failure import parse_chain_summary
    from staff.run_link import result_summary

    root_id = rec.retry_of or rec.id
    append_chain_attempt(store, root_id, provider=provider, model=model, outcome="succeeded")
    summary = (rec.outcome or "") or result_summary(result_line)
    refreshed = store.get_run(root_id)
    chain_raw = getattr(refreshed, "provider_chain", "") if refreshed else ""
    chain_note = parse_chain_summary(parse_chain_log(chain_raw))
    if chain_note and summary:
        return f"{chain_note}. {summary}"
    return str(chain_note or summary or "")


def resolve_provider_and_model(
    adapters: Mapping[str, ProviderAdapter],
    machine: str,
    req: RunRequest,
    role: RoleSpec,
) -> tuple[str, str | None, str, list[dict[str, str]]]:
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
        if _provider_auth_blocks(provider, machine):
            provider, model, tier, _skipped = select_provider_from_chain(
                adapters,
                role,
                req=req,
                requested_provider=req.provider,
                ceiling=ceiling,
                quota_headroom=_headroom,
                machine=machine,
            )
            return provider, model, tier, _skipped
    else:
        provider, model, tier, skipped = select_provider_from_chain(
            adapters,
            role,
            req=req,
            requested_provider=None,
            ceiling=ceiling,
            quota_headroom=_headroom,
            machine=machine,
        )
        return provider, model, tier, skipped
    if provider not in adapters:
        raise ValueError(f"unknown provider '{provider}'")
    if req.provider and req.provider not in role.providers and role.name != "ad-hoc":
        allowed = ", ".join(role.providers)
        raise ValueError(f"provider '{req.provider}' is not allowed for role '{role.name}' (allowed: {allowed})")
    if provider_switch_mod.is_disabled(provider):
        raise ValueError(f"provider '{provider}' is {provider_switch_mod.DISABLED_DETAIL}")
    if not getattr(adapters[provider], "unattended", True):
        raise ValueError(f"provider '{provider}' is chat-only: it cannot run unattended without bypassing permissions")
    from staff.failover_model import resolve_launch_model, role_base_tier

    model, tier = resolve_launch_model(
        req=req,
        role=role,
        provider=provider,
        adapter=adapters[provider],
        step=None,
        active_tier=role_base_tier(role),
    )
    return provider, model, tier, []


def select_provider_from_chain(
    adapters: Mapping[str, ProviderAdapter],
    role: RoleSpec,
    *,
    req: RunRequest | None,
    requested_provider: str | None,
    ceiling: float | None,
    quota_headroom: Callable[[str, float | None], bool],
    machine: str = "local",
) -> tuple[str, str | None, str, list[dict[str, str]]]:
    """Resolve provider + model + tier for launch using failover chain and cooldown skip."""
    from staff.failover_model import resolve_launch_model, role_base_tier

    auth_skipped: list[dict[str, str]] = []
    chain = resolve_failover_chain(role, requested_provider=requested_provider)
    step = pick_launch_step(chain, adapters, role, skip_cooling=True, machine=machine, auth_skipped=auth_skipped)
    if step is None:
        step = pick_launch_step(chain, adapters, role, skip_cooling=False, machine=machine, auth_skipped=auth_skipped)
    if step is None:
        for candidate in chain:
            if _provider_auth_blocks(candidate.provider, machine):
                auth_skipped.append({"provider": candidate.provider, "outcome": "auth_expired"})
                continue
            if _usable_provider(adapters, role, candidate.provider):
                step = candidate
                break
    if step is None:
        for candidate in chain:
            if not _provider_auth_blocks(candidate.provider, machine) and _usable_provider(
                adapters, role, candidate.provider
            ):
                step = candidate
                break
        if step is None and chain:
            step = chain[0]
        elif step is None and role.providers:
            step = FailoverStep(role.providers[0], role.model)
    provider = step.provider
    if not quota_headroom(provider, ceiling):
        for alt in chain:
            if alt.provider != provider and quota_headroom(alt.provider, ceiling):
                if (
                    not provider_in_cooldown(alt.provider)
                    and not _provider_auth_blocks(alt.provider, machine)
                    and _usable_provider(adapters, role, alt.provider)
                ):
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
    return provider, model, tier, auth_skipped
