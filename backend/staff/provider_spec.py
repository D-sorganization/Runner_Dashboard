"""Staff launch provider registry (#4267 slice 1).

Single RD source for staff CLI provider metadata derived from ``staff.adapters.ADAPTERS``.
Repository_Management provider launch settings are generated from this table, not the reverse
(owner decision #4258, 2026-11-10).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

from staff.adapter_policies import _CHAT_READ_ONLY_FLAGS, READ_ONLY_RUN_PROVIDERS
from staff.adapters import ADAPTERS, ProviderAdapter

TRANSCRIPT_REL_PATH = ".staff/transcript.log"
RM_CONTRACT_SCHEMA = "staff-provider-spec.v1"

# Subscription-seat providers that must fail closed on auth preflight (#4267 A-12).
SUBSCRIPTION_OAUTH_PROVIDERS = frozenset({"claude", "codex", "antigravity", "gemini", "grok", "cursor-agent"})

# DeepSeek remains behind #4231 — never part of the staff launch registry.
_EXCLUDED_PROVIDER_IDS = frozenset({"deepseek", "deepseek-api", "opencode"})

# Provider-keyed tables that must carry exactly the staff launch registry key set (#4267).
FULL_REGISTRY_PARITY_TABLES: tuple[str, ...] = ("known_providers", "model_tiers")

# Provider-keyed tables that may list a subset but never ids outside the registry (#4267).
SUBSET_REGISTRY_PARITY_TABLES: tuple[str, ...] = (
    "failover_default_chain",
    "auth_preflight_probes",
    "classifier_login_commands",
    "classifier_upgrade_commands",
    "review_provider_family",
)

# Pre-registry author ids kept for same-provider review matching only.
REVIEW_FAMILY_LEGACY_PROVIDER_KEYS = frozenset({"grok-chat"})


@dataclass(frozen=True)
class ProviderSpec:
    """Static launch contract for one staff provider (argv-first, behavior-neutral)."""

    provider_id: str
    label: str
    executable: str
    argv_template: tuple[str, ...]
    prompt_via_stdin: bool
    prompt_channel: str
    json_lines: bool
    default_model: str | None
    models: tuple[str, ...]
    unattended: bool
    read_only_run: bool
    lease_agent: str
    chat_read_only_argv: tuple[str, ...]
    skill_export_provider_key: str
    transcript_relpath: str
    subscription_oauth: bool
    headless_flags: tuple[str, ...]
    auth_probe_id: str
    failure_pattern_hints: tuple[str, ...]


def _headless_flags(argv: tuple[str, ...]) -> tuple[str, ...]:
    interesting = (
        "--permission-mode",
        "--sandbox",
        "--mode",
        "--approval-mode",
        "--output-format",
        "--allow",
        "--deny",
    )
    out: list[str] = []
    for part in argv:
        if part in interesting:
            out.append(part)
        if part.startswith("--") and "auto" in part.lower():
            out.append(part)
    return tuple(out)


def _spec_from_adapter(adapter: ProviderAdapter) -> ProviderSpec:
    chat_flags = _CHAT_READ_ONLY_FLAGS.get(adapter.provider_id, ())
    return ProviderSpec(
        provider_id=adapter.provider_id,
        label=adapter.label,
        executable=adapter.executable,
        argv_template=tuple(adapter.argv),
        prompt_via_stdin=adapter.prompt_via_stdin,
        prompt_channel="stdin" if adapter.prompt_via_stdin else "argv",
        json_lines=adapter.json_lines,
        default_model=adapter.default_model,
        models=tuple(adapter.models),
        unattended=bool(adapter.unattended),
        read_only_run=adapter.provider_id in READ_ONLY_RUN_PROVIDERS,
        lease_agent=adapter.lease_agent,
        chat_read_only_argv=chat_flags,
        skill_export_provider_key=adapter.provider_id.strip().lower() or "generic",
        transcript_relpath=TRANSCRIPT_REL_PATH,
        subscription_oauth=adapter.provider_id in SUBSCRIPTION_OAUTH_PROVIDERS,
        headless_flags=_headless_flags(adapter.argv),
        auth_probe_id=adapter.lease_agent,
        failure_pattern_hints=(
            "authentication required",
            "rate limit",
            "quota",
            "500 internal server error",
        ),
    )


@lru_cache(maxsize=1)
def staff_provider_specs() -> dict[str, ProviderSpec]:
    """Built once from ``ADAPTERS``; excludes holds (#4231 DeepSeek)."""
    specs = {pid: _spec_from_adapter(ad) for pid, ad in ADAPTERS.items() if pid not in _EXCLUDED_PROVIDER_IDS}
    return specs


def staff_provider_ids() -> frozenset[str]:
    return frozenset(staff_provider_specs())


def known_staff_providers() -> frozenset[str]:
    """Alias for parity tables historically named ``KNOWN_PROVIDERS``."""
    return staff_provider_ids()


def lease_agents_for_roster() -> frozenset[str]:
    """Lease agent ids that must be rankable in fleet roster tables."""
    return frozenset(spec.lease_agent for spec in staff_provider_specs().values())


def export_rm_provider_contract() -> dict[str, Any]:
    """JSON payload RM ingests to synchronize provider launch settings (RD is source)."""
    providers = [
        {k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(spec).items()}
        for spec in sorted(staff_provider_specs().values(), key=lambda s: s.provider_id)
    ]
    return {
        "schema_version": RM_CONTRACT_SCHEMA,
        "generated_from": "backend/staff/adapters.py",
        "providers": providers,
    }


def export_rm_provider_contract_json() -> str:
    return json.dumps(export_rm_provider_contract(), indent=2, sort_keys=True)


def parity_provider_id(provider_id: str) -> str:
    """Normalize dispatch/classifier spellings to staff registry ids for parity checks."""
    import provider_switch as provider_switch_mod

    raw = provider_id.strip()
    if raw in staff_provider_specs():
        return raw
    target = provider_switch_mod.canonical_id(raw)
    for registry_id in staff_provider_ids():
        if provider_switch_mod.canonical_id(registry_id) == target:
            return registry_id
    return raw


def collect_provider_keyed_table_ids() -> dict[str, frozenset[str]]:
    """Provider-keyed tables that must stay aligned with :func:`staff_provider_ids`."""
    from staff.classifier import LOGIN_COMMANDS, UPGRADE_COMMANDS
    from staff.model_tiers import _load_tier_config
    from staff.panel_models import PANEL_PROVIDERS
    from staff.pricing import DEFAULT_MODEL, PRICE_TABLE, WALL_TIME_USD_PER_MINUTE
    from staff.provider_auth_preflight import _PROBE_PROVIDERS
    from staff.provider_failover_config import default_failover_chain
    from staff.review import PROVIDER_FAMILY

    registry = staff_provider_ids()
    tier_table = _load_tier_config().get("models_by_tier") or {}
    tier_providers: set[str] = set()
    if isinstance(tier_table, dict):
        for tier_rows in tier_table.values():
            if isinstance(tier_rows, dict):
                tier_providers.update(str(k) for k in tier_rows)
    return {
        "registry": registry,
        "known_providers": known_staff_providers(),
        "failover_default_chain": frozenset(step.provider for step in default_failover_chain()),
        "model_tiers": frozenset(tier_providers),
        "panel_providers": frozenset(PANEL_PROVIDERS),
        "price_table": frozenset(PRICE_TABLE),
        "default_model_table": frozenset(DEFAULT_MODEL),
        "wall_time_rates": frozenset(WALL_TIME_USD_PER_MINUTE),
        "auth_preflight_probes": frozenset(_PROBE_PROVIDERS),
        "lease_agents": lease_agents_for_roster(),
        "classifier_login_commands": frozenset(parity_provider_id(k) for k in LOGIN_COMMANDS),
        "classifier_upgrade_commands": frozenset(UPGRADE_COMMANDS),
        "review_provider_family": frozenset(PROVIDER_FAMILY),
    }
