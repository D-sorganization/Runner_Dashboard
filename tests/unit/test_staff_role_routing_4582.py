"""#4582: pm-sidekick Tools repo map; offload Brick schedule homes across worker nodes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from staff import role_routing_config as rrc
from staff import schedule_placement as sp
from staff.role_provider_chains import _CONFIG_PATH as PROVIDER_CHAINS_PATH
pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROVIDER_CHAINS_SNAPSHOT = PROVIDER_CHAINS_PATH.read_text(encoding="utf-8")


def test_reference_repo_routing_maps_pm_sidekick_to_tools() -> None:
    routing = rrc.load_reference_repo_routing()
    assert routing["roles"]["pm-sidekick"]["repos"] == ["Tools"]


def test_reference_schedule_placements_offload_brick_remediator_roles() -> None:
    placements = rrc.load_reference_schedule_placements()
    brick_roles = frozenset(rrc.brick_offload_roles())
    for role in brick_roles:
        assert placements[role] != rrc.BRICK_HOST
    assert placements["night-watch"] in {"node-a", "node-b"}
    assert placements["pr-remediator"] in {"node-a", "node-b"}
    assert placements["fleet-critic"] in {"node-a", "node-b"}
    assert placements["pm-sidekick"] in rrc.worker_schedule_hosts()


def test_desk_is_fallback_only_not_primary_home_for_offloaded_roles() -> None:
    placements = rrc.load_reference_schedule_placements()
    desk = rrc.desk_fallback_host()
    for role in rrc.brick_offload_roles():
        assert placements[role] != desk


def test_brick_migration_targets_list_desk_last() -> None:
    targets = rrc.brick_offload_migration_targets()
    assert targets[-1] == rrc.desk_fallback_host()
    assert rrc.BRICK_HOST not in targets
    assert targets[:2] == ("node-a", "node-b")


def test_validate_fleet_routing_policy_passes_on_repo_reference_configs() -> None:
    problems = rrc.validate_fleet_routing_policy()
    assert problems == []


def test_repo_routing_enrichment_overrides_stale_rm_sidekick_repo() -> None:
    raw = {
        "name": "pm-sidekick",
        "title": "PM-Sidekick",
        "providers": ["claude", "codex", "cursor-agent"],
        "permissions": {"code_read_only": True},
        "repos": ["Sidekick"],
    }
    enriched = rrc.enrich_role_repo_routing(raw)
    assert enriched["repos"] == ["Tools"]
    assert raw["repos"] == ["Sidekick"]


def test_provider_chains_unchanged_for_offloaded_roles() -> None:
    """#4582: policy file is not edited — only placement/repo routing references."""
    data = json.loads(_PROVIDER_CHAINS_SNAPSHOT)
    assert data["roles"]["night-watch"] == ["codex", "claude", "cursor-agent"]
    assert data["roles"]["pr-remediator"] == ["codex", "cursor-agent", "claude"]
    assert data["roles"]["fleet-critic"] == ["claude", "codex", "cursor-agent"]
    assert data["roles"]["pm-sidekick"] == ["claude", "codex", "cursor-agent"]


def test_migrate_off_brick_using_policy_targets(tmp_path: Path) -> None:
    from staff.role_spec import RoleSpec

    reg = {
        "version": 1,
        "machines": [
            {"name": "node-c", "portable": True},
            {"name": "node-a", "staff": {"roles_enabled": ["*"]}},
            {"name": "node-b", "staff": {"roles_enabled": ["*"]}},
            {"name": "hub-a", "staff": {"roles_enabled": ["*"]}},
        ],
    }
    roles = {
        "night-watch": RoleSpec(
            name="night-watch",
            title="night-watch",
            schedule="0 22 * * *",
            providers=("codex",),
        ),
        "pr-remediator": RoleSpec(
            name="pr-remediator",
            title="pr-remediator",
            schedule="0 3 * * *",
            providers=("codex",),
        ),
        "fleet-critic": RoleSpec(
            name="fleet-critic",
            title="fleet-critic",
            schedule="0 4 * * *",
            providers=("claude",),
        ),
    }
    placements = {name: "node-c" for name in roles}
    state = {name: {"cursor": "1"} for name in roles}
    result = sp.migrate_placements_off_host(
        rrc.BRICK_HOST,
        roles=roles,
        placements=placements,
        state=state,
        registry=reg,
        targets=rrc.brick_offload_migration_targets(),
    )
    assert len(result.moved) == 3
    assert {host for _, host in result.moved} == {"node-a"}
    assert all(placements[r] != "node-c" for r in roles)


def test_pm_sidekick_persona_still_documents_packaging_hold() -> None:
    from staff import pm_sidekick_persona as ps

    raw = ps.pm_sidekick_role_data()
    assert raw["repos"] == ["Tools"]
    assert "sidekick packaging" in raw["instructions"].lower()
    assert "hold stays separate" in raw["instructions"].lower()