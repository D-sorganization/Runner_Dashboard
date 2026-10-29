"""anthropic-api provider adapter: secret loading and inert status (issue #3123 slice 2).

Ships fully inert: no key exists yet, and nothing here spawns a request. The
key's value is never logged, returned, or embedded in any status/detail
string this module produces — only its env var *name* and presence/absence
are observable from outside. Slice 3 adds ``staff/summary`` reporting and
alerts; slice 4 wires this into the live provider registry and Settings UI.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from staff.anthropic_api_budget import AnthropicApiBudgetGate
from vault_store import resolve_vault_handle

DEFAULT_KEY_ENV_VAR = "ANTHROPIC_API_KEY"
_KEY_ENV_VAR_OVERRIDE = "ANTHROPIC_API_KEY_ENV_VAR"
_VAULT_ACTOR = "anthropic-api-provider"

ProviderStatus = Literal["not_configured", "ready", "budget_exhausted", "error"]


def key_env_var(env: Mapping[str, str] | None = None) -> str:
    """Return the env var name the key is read from (configurable, #3123 req. 2).

    Post: a non-empty string, defaulting to :data:`DEFAULT_KEY_ENV_VAR`.
    """
    env_map = env if env is not None else os.environ
    override = env_map.get(_KEY_ENV_VAR_OVERRIDE, "").strip()
    return override or DEFAULT_KEY_ENV_VAR


def load_api_key(env: Mapping[str, str] | None = None) -> str | None:
    """Return the Anthropic API key, or ``None`` when unconfigured.

    Checks the hub-host env first (``env`` override for tests; this is the
    same ``~/.config/runner-dashboard/env`` process environment every other
    RD secret reads from, e.g. ``LINEAR_API_KEY``), then falls back to the
    encrypted vault under the same var name. Never logs or returns anything
    about *why* a vault lookup failed beyond "not found" (DbC: callers only
    ever see a key or ``None``, never a partial value or stack trace).
    """
    env_map = env if env is not None else os.environ
    var_name = key_env_var(env_map)
    value = env_map.get(var_name, "").strip()
    if value:
        return value
    try:
        vault_value = resolve_vault_handle(f"$vault:{var_name}", actor=_VAULT_ACTOR)
    except (KeyError, PermissionError):
        return None
    return vault_value.strip() or None


@dataclass(frozen=True, slots=True)
class AnthropicApiProviderState:
    """Pure projection of provider readiness. Never carries the key itself."""

    status: ProviderStatus
    detail: str


def provider_state(
    *,
    api_key: str | None,
    budget_gate: AnthropicApiBudgetGate | None = None,
    estimated_cost_usd: float = 0.0,
) -> AnthropicApiProviderState:
    """Derive the provider's reported status without ever touching the key's value.

    Pre: ``estimated_cost_usd >= 0``.
    Post: ``status`` is one of the four documented literals; ``detail`` never
    contains ``api_key`` (it is only ever used in a truthiness check here).
    """
    assert estimated_cost_usd >= 0.0, "estimated_cost_usd must be non-negative"  # noqa: S101
    if not api_key:
        return AnthropicApiProviderState(
            status="not_configured",
            detail=f"Set {key_env_var()} in ~/.config/runner-dashboard/env or the vault.",
        )
    if budget_gate is not None:
        ok, reason = budget_gate.can_spend(estimated_cost_usd)
        if not ok:
            return AnthropicApiProviderState(status="budget_exhausted", detail=reason)
    return AnthropicApiProviderState(status="ready", detail="Key configured; budget available.")
