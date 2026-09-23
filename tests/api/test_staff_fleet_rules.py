"""Built-in fleet rules carried by every staff prompt (issue #1217)."""

from __future__ import annotations

from staff import workspace
from staff.roles import RoleSpec


def _role() -> RoleSpec:
    return RoleSpec(name="night-watch", title="Night Watch", instructions="Overnight triage.")


def test_fleet_rules_carry_all_agent_guardrails() -> None:
    rules = workspace.FLEET_RULES
    assert "never merge" in rules
    assert "claim:local" in rules
    assert "live lease" in rules
    assert "bulk remediation issues" in rules


def test_compose_prompt_always_ends_with_fleet_rules() -> None:
    prompt = workspace.compose_prompt(
        _role(), repo="Tools", target_ref="issue #1", operator_prompt="", branch="staff/night-watch-1"
    )
    assert prompt.endswith(workspace.FLEET_RULES)
    assert "claim:local" in prompt


def test_compose_prompt_inlines_playbook_from_rm_root(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "check_agent_claim.py").write_text("", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "fleet-night-watch.md").write_text("# Night Watch\n\nStep 1: triage.", encoding="utf-8")
    monkeypatch.setenv("STAFF_RM_ROOT", str(tmp_path))
    role = RoleSpec(name="night-watch", title="Night Watch", playbook="docs/fleet-night-watch.md")
    prompt = workspace.compose_prompt(role, repo="Tools", target_ref="", operator_prompt="x", branch="b")
    assert "Step 1: triage." in prompt
    assert workspace.playbook_text("../etc/passwd") == ""
    assert workspace.playbook_text("docs/missing.md") == ""
