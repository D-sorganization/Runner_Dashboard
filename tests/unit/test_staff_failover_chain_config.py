"""Failover chain config and resolution (#2047 slice 3a)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from staff.provider_failover_config import FailoverStep, default_failover_chain, resolve_failover_chain
from staff.roles import RoleSpec

pytestmark = pytest.mark.unit


def test_default_chain_order_matches_repo_config() -> None:
    chain = default_failover_chain()
    assert [s.provider for s in chain][:4] == ["codex", "claude", "antigravity", "cursor-agent"]


def test_resolve_chain_filters_to_role_providers() -> None:
    role = RoleSpec(name="worker", title="Worker", providers=("codex", "antigravity"))
    steps = resolve_failover_chain(role)
    assert [s.provider for s in steps] == ["codex", "antigravity"]


def test_resolve_default_chain_preserves_config_order_not_role_yaml_order() -> None:
    role = RoleSpec(name="ad-hoc", title="Ad-hoc", providers=("claude", "codex", "antigravity"))
    steps = resolve_failover_chain(role)
    assert [step.provider for step in steps] == ["codex", "claude", "antigravity"]


def test_role_failover_override_must_stay_in_providers() -> None:
    # RoleSpec gains failover_chain in slice 5b; resolve_failover_chain only needs attributes.
    role = SimpleNamespace(
        providers=("codex",),
        failover_chain=(FailoverStep("codex", "gpt-6-luna"),),
        model=None,
    )
    assert resolve_failover_chain(role) == (FailoverStep("codex", "gpt-6-luna"),)
