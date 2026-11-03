"""Staff schedule home host placement and portable-node guards (#3901)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from staff import schedule_placement as sp
from staff.role_spec import RoleSpec


def _role(name: str, *, retired: bool = False, schedule: str = "0 3 * * *", tier: str = "routine") -> RoleSpec:
    return RoleSpec(
        name=name,
        title=name,
        schedule=schedule,
        retired=retired,
        providers=("claude",),
        tier=tier,
    )


def _registry(*machines: dict) -> dict:
    return {"version": 1, "machines": list(machines)}


pytestmark = pytest.mark.unit


def test_portable_machine_never_allows_scheduled_roles() -> None:
    reg = _registry({"name": "brick", "portable": True, "staff": {"roles_enabled": ["*"]}})
    assert sp.role_schedule_allowed_on_machine("night-watch", "brick", reg) is False


def test_roles_enabled_list_gates_placement() -> None:
    reg = _registry(
        {"name": "desk", "staff": {"roles_enabled": ["night-watch", "maintenance"]}},
        {"name": "brick", "portable": True},
    )
    assert sp.role_schedule_allowed_on_machine("night-watch", "desk", reg)
    assert not sp.role_schedule_allowed_on_machine("cartographer", "desk", reg)


def test_validate_placement_rejects_disabled_host() -> None:
    reg = _registry({"name": "desk", "staff": {"roles_enabled": ["maintenance"]}})
    with pytest.raises(ValueError, match="not enabled"):
        sp.validate_schedule_host("night-watch", "desk", reg, _role("night-watch"))


def test_validate_placement_rejects_retired_role() -> None:
    reg = _registry({"name": "desk", "staff": {"roles_enabled": ["*"]}})
    with pytest.raises(ValueError, match="retired"):
        sp.validate_schedule_host("archivist", "desk", reg, _role("archivist", retired=True))


def test_inventory_includes_role_cron_and_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "placements.json"
    path.write_text(json.dumps({"night-watch": "desk", "maintenance": "control-tower"}), encoding="utf-8")
    monkeypatch.setattr(sp, "placements_path", lambda: path)
    roles = {"night-watch": _role("night-watch", schedule="0 22 * * *"), "maintenance": _role("maintenance")}
    rows = sp.schedule_inventory("brick", roles, placements={"night-watch": "desk", "maintenance": "control-tower"})
    by_role = {r["role"]: r for r in rows}
    assert by_role["night-watch"] == {"role": "night-watch", "cron": "0 22 * * *", "host": "desk"}
    assert by_role["maintenance"]["host"] == "control-tower"


def test_schedule_host_skip_reason_when_home_is_peer() -> None:
    reg = _registry({"name": "desk", "staff": {"roles_enabled": ["*"]}}, {"name": "brick", "portable": True})
    placements = {"night-watch": "desk"}
    reason = sp.schedule_host_skip_reason("night-watch", "brick", placements, reg)
    assert reason and "desk" in reason


def test_migrate_off_host_drops_retired_and_rehomes_live(tmp_path: Path) -> None:
    reg = _registry(
        {"name": "brick", "portable": True},
        {"name": "desk", "staff": {"roles_enabled": ["*"]}},
        {"name": "control-tower", "staff": {"roles_enabled": ["*"]}, "hardware": {"workload_tags": ["memory-heavy"]}},
    )
    roles = {
        "archivist": _role("archivist", retired=True),
        "night-watch": _role("night-watch"),
        "heavy-job": _role("heavy-job", tier="complex"),
    }
    placements = {"archivist": "brick", "night-watch": "brick", "heavy-job": "brick"}
    state = {"archivist": {"cursor": "1"}, "night-watch": {"cursor": "2"}, "heavy-job": {"cursor": "3"}}
    result = sp.migrate_placements_off_host(
        "brick",
        roles=roles,
        placements=placements,
        state=state,
        registry=reg,
        targets=("desk", "control-tower"),
    )
    assert "archivist" in result.removed_roles
    assert "archivist" not in placements
    assert "archivist" not in state
    assert placements["heavy-job"] == "control-tower"
    assert placements["night-watch"] == "desk"
