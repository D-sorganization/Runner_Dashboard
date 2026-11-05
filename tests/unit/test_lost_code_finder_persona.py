"""Unit tests for Lost Code Finder RoleSpec, read-only permissions, and holds guardrails (#3866)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import staff.lost_code_finder_persona as lcf
from staff.holds import Hold, matches, seed_from_roles
from staff.roles import clear_roles_cache, load_roles
from staff.validator import validate_role_data

pytestmark = pytest.mark.unit
LA = ZoneInfo("America/Los_Angeles")


def test_lost_code_finder_yaml_passes_schema_and_loads_read_only(tmp_path: Path) -> None:
    assert lcf.write_lost_code_finder_role_yaml(tmp_path).name == "lost-code-finder.yml"
    spec = lcf.load_lost_code_finder_role_spec(tmp_path)
    assert spec.valid, spec.errors
    raw = lcf.load_lost_code_finder_role_spec(tmp_path, raw=True)
    assert validate_role_data(raw) == []
    perms = raw["permissions"]
    assert perms["code_read_only"] is True
    assert not any(perms[k] for k in ("lease", "push_branch", "open_pr", "merge", "host_shell", "notify_user"))
    assert raw["reports_to"] == "barb"
    assert raw["holds"] == list(lcf.LOST_CODE_FINDER_HOLDS)
    assert raw["scope"]["ledger_only"] is True
    assert "dispatch" not in str(raw["instructions"]).lower() or "never" in raw["instructions"].lower()
    clear_roles_cache()
    role = load_roles(tmp_path)["lost-code-finder"]
    assert role.valid and role.code_read_only is True
    assert tuple(role.holds) == lcf.LOST_CODE_FINDER_HOLDS


def test_lost_code_finder_weekly_slot_computed_in_la(tmp_path: Path) -> None:
    lcf.write_lost_code_finder_role_yaml(tmp_path)
    role = lcf.load_lost_code_finder_role_spec(tmp_path)
    slots = lcf.next_lost_code_finder_slots(datetime(2026, 11, 2, 12, 0, tzinfo=LA), role)
    assert slots["weekly_audit"].startswith("2026-11-08T06:00:00")


def test_lost_code_finder_holds_seed_guardrails_and_respect_schedule_matches(tmp_path: Path) -> None:
    lcf.write_lost_code_finder_role_yaml(tmp_path)
    clear_roles_cache()
    roles = load_roles(tmp_path)
    guardrails = seed_from_roles(roles)
    texts = {h.text for h in guardrails if lcf.ROLE in h.applies_to}
    assert texts == set(lcf.LOST_CODE_FINDER_HOLDS)
    assert all(h.kind == "guardrail" for h in guardrails)

    deploy_hold = Hold(
        id="rd-2657",
        text="RD #2657: no deploy or release",
        applies_to=["*"],
        kind="schedule",
    )
    assert matches(deploy_hold, lcf.ROLE, "Runner_Dashboard")
    sidekick_hold = Hold(
        id="sidekick-packaging",
        text="Sidekick packaging HOLD stays",
        applies_to=["repo:Tools"],
        kind="schedule",
    )
    assert matches(sidekick_hold, lcf.ROLE, "Tools")
    assert not matches(sidekick_hold, lcf.ROLE, "Runner_Dashboard")
