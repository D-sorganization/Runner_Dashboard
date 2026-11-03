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
    reg = _registry({"name": "node-c", "portable": True, "staff": {"roles_enabled": ["*"]}})
    assert sp.role_schedule_allowed_on_machine("night-watch", "node-c", reg) is False


def test_roles_enabled_list_gates_placement() -> None:
    reg = _registry(
        {"name": "hub-a", "staff": {"roles_enabled": ["night-watch", "maintenance"]}},
        {"name": "node-c", "portable": True},
    )
    assert sp.role_schedule_allowed_on_machine("night-watch", "hub-a", reg)
    assert not sp.role_schedule_allowed_on_machine("cartographer", "hub-a", reg)


def test_validate_placement_rejects_disabled_host() -> None:
    reg = _registry({"name": "hub-a", "staff": {"roles_enabled": ["maintenance"]}})
    with pytest.raises(ValueError, match="not enabled"):
        sp.validate_schedule_host("night-watch", "hub-a", reg, _role("night-watch", tier="routine"))


def test_validate_placement_rejects_retired_role() -> None:
    reg = _registry({"name": "hub-a", "staff": {"roles_enabled": ["*"]}})
    with pytest.raises(ValueError, match="retired"):
        sp.validate_schedule_host("archivist", "hub-a", reg, _role("archivist", retired=True))


def test_inventory_includes_role_cron_and_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "placements.json"
    path.write_text(json.dumps({"night-watch": "hub-a", "maintenance": "node-a"}), encoding="utf-8")
    monkeypatch.setattr(sp, "placements_path", lambda: path)
    roles = {
        "night-watch": _role("night-watch", schedule="0 22 * * *", tier="routine"),
        "maintenance": _role("maintenance", tier="routine"),
    }
    rows = sp.schedule_inventory("node-c", roles, placements={"night-watch": "hub-a", "maintenance": "node-a"})
    by_role = {r["role"]: r for r in rows}
    assert by_role["night-watch"] == {"role": "night-watch", "cron": "0 22 * * *", "host": "hub-a"}
    assert by_role["maintenance"]["host"] == "node-a"


def test_schedule_host_skip_reason_when_home_is_peer() -> None:
    reg = _registry({"name": "hub-a", "staff": {"roles_enabled": ["*"]}}, {"name": "node-c", "portable": True})
    placements = {"night-watch": "hub-a"}
    reason = sp.schedule_host_skip_reason("night-watch", "node-c", placements, reg)
    assert reason and "hub-a" in reason
