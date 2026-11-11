"""Slice 1 (#4267): staff ``ProviderSpec`` registry and cross-table parity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from coordination import roster
from staff.adapter_policies import _CHAT_READ_ONLY_FLAGS
from staff.adapters import ADAPTERS, ProviderAdapter
from staff.provider_spec import (
    RM_CONTRACT_SCHEMA,
    collect_provider_keyed_table_ids,
    export_rm_provider_contract,
    staff_provider_ids,
    staff_provider_specs,
)

pytestmark = pytest.mark.unit

_LOCK_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "staff_provider_launch_recipes.v4267.json"


def _launch_recipe(adapter: ProviderAdapter) -> dict[str, Any]:
    env_keys = set(adapter.extra_env.keys())
    if adapter.env_builder:
        env_keys |= set(adapter.runtime_env().keys())
    return {
        "executable": adapter.executable,
        "argv_template": list(adapter.argv),
        "prompt_via_stdin": adapter.prompt_via_stdin,
        "env_keys": sorted(env_keys),
        "chat_read_only_argv": list(_CHAT_READ_ONLY_FLAGS.get(adapter.provider_id, ())),
        "lease_agent": adapter.lease_agent,
    }


def _load_locked_launch_recipes() -> dict[str, dict[str, Any]]:
    raw = json.loads(_LOCK_PATH.read_text(encoding="utf-8"))
    providers = raw.get("providers")
    if not isinstance(providers, dict):
        raise AssertionError(f"{_LOCK_PATH}: missing providers object")
    return providers


def test_launch_recipes_locked_to_pre_wp13_golden() -> None:
    """Fail when any staff launch recipe drifts (executable/argv/env) without updating the golden file."""
    locked = _load_locked_launch_recipes()
    missing = sorted(staff_provider_ids() - frozenset(locked))
    assert not missing, f"golden file missing launch recipes for: {missing}"
    extra = sorted(set(locked) - staff_provider_ids())
    assert not extra, f"golden file has stale providers (excluded from registry): {extra}"

    drift: list[str] = []
    for pid in sorted(staff_provider_ids()):
        live = _launch_recipe(ADAPTERS[pid])
        if live != locked[pid]:
            drift.append(pid)
        spec = staff_provider_specs()[pid]
        if spec.executable != locked[pid]["executable"]:
            drift.append(f"{pid}:spec.executable")
        if list(spec.argv_template) != locked[pid]["argv_template"]:
            drift.append(f"{pid}:spec.argv_template")
    assert not drift, (
        "Launch recipe drifted vs tests/fixtures/staff_provider_launch_recipes.v4267.json — "
        f"update the golden intentionally or revert the adapter change: {drift}"
    )


def test_specs_cover_adapters_without_deepseek() -> None:
    expected = {pid for pid in ADAPTERS if pid not in {"deepseek", "deepseek-api", "opencode"}}
    assert staff_provider_ids() == frozenset(expected)
    for pid in staff_provider_ids():
        spec = staff_provider_specs()[pid]
        adapter = ADAPTERS[pid]
        assert spec.executable == adapter.executable
        assert spec.argv_template == adapter.argv
        assert spec.lease_agent == adapter.lease_agent


def test_known_providers_and_tiers_match_registry() -> None:
    tables = collect_provider_keyed_table_ids()
    registry = tables["registry"]
    assert tables["known_providers"] == registry
    assert tables["model_tiers"] == registry


def test_failover_chain_and_probes_stay_in_registry() -> None:
    tables = collect_provider_keyed_table_ids()
    registry = tables["registry"]
    assert tables["failover_default_chain"] <= registry
    assert tables["auth_preflight_probes"] <= registry
    assert "grok" in registry
    assert "anthropic-api" in registry
    assert "grok-chat" not in registry


def test_lease_agents_are_roster_rankable() -> None:
    tables = collect_provider_keyed_table_ids()
    roster_ids = set(roster.agent_ids())
    missing = [agent for agent in tables["lease_agents"] if agent not in roster_ids]
    assert not missing, f"lease agents missing from coordination roster: {missing}"


def test_rm_contract_generated_from_rd_specs() -> None:
    payload = export_rm_provider_contract()
    assert payload["schema_version"] == RM_CONTRACT_SCHEMA
    assert payload["generated_from"] == "backend/staff/adapters.py"
    ids = {row["provider_id"] for row in payload["providers"]}
    assert ids == staff_provider_ids()
    text = json.dumps(payload)
    assert "deepseek" not in text
