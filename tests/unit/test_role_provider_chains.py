"""Dieter-approved staff role provider chains (2026-11-13)."""

from __future__ import annotations

import pytest
from staff.provider_failover_config import FailoverStep, resolve_failover_chain
from staff.role_provider_chains import (
    enrich_role_provider_policy,
    infer_author_provider_from_branch,
    is_antigravity_restricted_repo,
    reset_role_provider_chains_cache,
)
from staff.role_spec import parse_role

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_cfg() -> None:
    reset_role_provider_chains_cache()


def test_enrich_applies_chain_when_yaml_has_no_failover_chain() -> None:
    raw = {
        "name": "night-watch",
        "title": "Night Watch",
        "providers": ["codex", "cursor-agent", "claude"],
    }
    enriched = enrich_role_provider_policy(raw)
    chain = enriched["retry"]["failover_chain"]
    assert [s["provider"] for s in chain] == ["codex", "claude", "cursor-agent"]
    assert enriched["providers"] == ["codex", "claude", "cursor-agent"]


def test_enrich_skips_unchanged_roles() -> None:
    raw = {"name": "rd-barb", "title": "Barb", "providers": ["claude"]}
    assert enrich_role_provider_policy(raw) is raw


def test_enrich_skips_fake_provider_fixtures() -> None:
    raw = {"name": "night-watch", "title": "Night Watch", "providers": ["fake", "claude"]}
    assert enrich_role_provider_policy(raw) is raw
    bare = {"name": "pr-remediator", "title": "PR Remediator", "repos": ["UpstreamDrift"]}
    assert enrich_role_provider_policy(bare) is bare


def test_antigravity_denied_on_restricted_repos() -> None:
    assert is_antigravity_restricted_repo("Gasification_Model")
    assert is_antigravity_restricted_repo("org/SBIR_Foo")
    assert not is_antigravity_restricted_repo("Runner_Dashboard")


def test_resolve_chain_drops_antigravity_for_gasification() -> None:
    role = parse_role(
        {
            "name": "research-scout",
            "title": "RS",
            "providers": ["codex", "claude", "antigravity"],
            "retry": {
                "failover_chain": [
                    {"provider": "codex", "model": "gpt-6.1-sol"},
                    {"provider": "claude", "model": "claude-opus-5-5"},
                    {"provider": "antigravity", "model": "gemini-3.8-flash-high"},
                ]
            },
        },
        "research-scout.yml",
    )
    chain = resolve_failover_chain(role, repo="Gasification_Model")
    assert [s.provider for s in chain] == ["codex", "claude"]


@pytest.mark.parametrize(
    ("branch", "provider"),
    [
        ("claude/fix-1", "claude"),
        ("codex/wp-9", "codex"),
        ("cursor/foo-87d5", "cursor-agent"),
        ("gemini/lane", "antigravity"),
    ],
)
def test_infer_author_from_branch_prefix(branch: str, provider: str) -> None:
    assert infer_author_provider_from_branch(branch) == provider


def test_all_policy_roles_parse_cleanly() -> None:
    from staff.role_provider_chains import approved_chain_for_role

    for role in (
        "code-reviewer",
        "night-watch",
        "pr-remediator",
        "librarian",
        "research-scout",
    ):
        chain = approved_chain_for_role(role)
        assert chain
        spec = parse_role(
            enrich_role_provider_policy({"name": role, "title": role, "providers": list(chain)}),
            f"{role}.yml",
        )
        assert spec.valid, spec.errors
        resolved = resolve_failover_chain(spec, repo="Runner_Dashboard")
        assert resolved
        assert resolved[0] == FailoverStep(chain[0], resolved[0].model)
