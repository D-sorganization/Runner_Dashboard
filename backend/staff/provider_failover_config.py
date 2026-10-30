"""Repo-managed default staff provider failover chain (#2047).

Roles may override with ``retry.failover_chain`` (or top-level ``failover_chain``);
every step must stay within the role's ``providers`` list (tighten only, never widen).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from staff.adapter_policies import READ_ONLY_RUN_PROVIDERS

# RoleSpec is passed at runtime; avoid importing staff.roles (cycle).

log = logging.getLogger("dashboard.staff.provider_failover_config")

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "provider_failover.json"


@dataclass(frozen=True)
class FailoverStep:
    provider: str
    model: str | None = None


def _load_raw() -> dict[str, Any]:
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("provider_failover.json unreadable (%s); using built-in defaults", exc)
        return {}
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=1)
def failover_cooldown_seconds() -> dict[str, float]:
    raw = _load_raw().get("cooldown_seconds") or {}
    defaults = {"provider_capacity": 600.0, "quota_exhausted": 3600.0, "rate_limited": 120.0}
    if not isinstance(raw, dict):
        return defaults
    out = dict(defaults)
    for key, val in raw.items():
        try:
            out[str(key)] = float(val)
        except (TypeError, ValueError):
            continue
    return out


def _builtin_chain() -> tuple[FailoverStep, ...]:
    return (
        FailoverStep("codex", "gpt-6-luna"),
        FailoverStep("claude", None),
        FailoverStep("anthropic-api", None),
        FailoverStep("antigravity", "gemini-3.8-flash-high"),
        FailoverStep("cursor-agent", None),
    )


@lru_cache(maxsize=1)
def default_failover_chain() -> tuple[FailoverStep, ...]:
    raw = _load_raw().get("default_chain")
    if not isinstance(raw, list) or not raw:
        return _builtin_chain()
    steps: list[FailoverStep] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        prov = str(entry.get("provider") or "").strip()
        if not prov:
            continue
        model_raw = entry.get("model")
        model = str(model_raw).strip() if model_raw not in (None, "", "default") else None
        steps.append(FailoverStep(prov, model))
    return tuple(steps) if steps else _builtin_chain()


def parse_role_failover_chain(data: dict[str, Any]) -> tuple[FailoverStep, ...] | None:
    retry_cfg = data.get("retry")
    raw = None
    if isinstance(retry_cfg, dict):
        raw = retry_cfg.get("failover_chain")
    if raw is None:
        raw = data.get("failover_chain")
    if raw is None:
        return None
    if not isinstance(raw, list):
        return ()
    steps: list[FailoverStep] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        prov = str(entry.get("provider") or "").strip()
        if not prov:
            continue
        model_raw = entry.get("model")
        model = str(model_raw).strip() if model_raw not in (None, "", "default") else None
        steps.append(FailoverStep(prov, model))
    return tuple(steps)


def validate_failover_chain(
    chain: tuple[FailoverStep, ...],
    role_providers: tuple[str, ...],
) -> list[str]:
    problems: list[str] = []
    allowed = frozenset(role_providers)
    for idx, step in enumerate(chain):
        if step.provider not in allowed:
            problems.append(f"failover_chain[{idx}].provider {step.provider!r} is not in role providers")
    return problems


def validate_failover_chain_data(data: dict[str, Any]) -> list[str]:
    providers = tuple(str(p).strip() for p in (data.get("providers") or ()) if str(p).strip())
    chain = parse_role_failover_chain(data)
    if chain is None:
        return []
    return validate_failover_chain(chain, providers)


def resolve_failover_chain(
    role: Any,
    *,
    requested_provider: str | None = None,
) -> tuple[FailoverStep, ...]:
    """Ordered failover steps for ``role``, filtered to allowed providers.

    When ``requested_provider`` is set, the chain starts at that provider (if allowed)
    and continues through later steps; earlier defaults are skipped.
    """
    raw_providers: tuple[str, ...] = tuple(role.providers)
    providers = raw_providers
    if getattr(role, "code_read_only", False):
        read_only = tuple(p for p in raw_providers if p in READ_ONLY_RUN_PROVIDERS)
        if read_only:
            providers = read_only
    role_model: str | None = getattr(role, "model", None)
    allowed = frozenset(providers)
    override = getattr(role, "failover_chain", ()) or ()
    default_chain = default_failover_chain()

    if override:
        raw_allowed = frozenset(raw_providers)
        filtered = [step for step in override if step.provider in raw_allowed]
    else:
        filtered = [step for step in default_chain if step.provider in allowed]
        included = {step.provider for step in filtered}
        filtered.extend(
            FailoverStep(pid, role_model if pid == providers[0] else None) for pid in providers if pid not in included
        )

    if not filtered:
        filtered = [FailoverStep(p, role_model if p == providers[0] else None) for p in providers]

    if requested_provider:
        req = requested_provider.strip().lower()
        for i, step in enumerate(filtered):
            if step.provider == req:
                return tuple(filtered[i:])
        if req in allowed:
            return (FailoverStep(req, role_model),) + tuple(s for s in filtered if s.provider != req)
    else:
        role_name = str(getattr(role, "name", "") or "").strip().lower()
        if role_name:
            from staff.rd_barb_provider_state import read_active_provider  # noqa: PLC0415

            snap = read_active_provider(role_name, allowed_providers=allowed)
            if snap is not None:
                matched = next((s for s in filtered if s.provider == snap.provider), None)
                active_model = snap.model if snap.model is not None else (matched.model if matched else None)
                head = FailoverStep(snap.provider, active_model)
                return (head, *(s for s in filtered if s.provider != snap.provider))
    return tuple(filtered)


def reset_failover_config_cache() -> None:
    default_failover_chain.cache_clear()
    failover_cooldown_seconds.cache_clear()
