"""Slice 1 (#4267): staff ``ProviderSpec`` registry and cross-table parity."""

from __future__ import annotations

import json

import pytest
from coordination import roster
from staff.adapters import ADAPTERS
from staff.provider_spec import (
    RM_CONTRACT_SCHEMA,
    export_rm_provider_contract,
    collect_provider_keyed_table_ids,
    staff_provider_ids,
    staff_provider_specs,
)

pytestmark = pytest.mark.unit


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
