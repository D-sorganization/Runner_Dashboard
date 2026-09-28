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
    assert "open pull request already references it" in rules  # #1225: no duplicate PRs for linked issues


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


# --- Barb live test 2026-09-27: runs must know where the dashboard's facts live ---


def test_unattended_prompt_points_at_local_dashboard_api(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("DASHBOARD_PORT", raising=False)
    prompt = workspace.compose_prompt(
        _role(), repo="", target_ref="", operator_prompt="One-line fleet status.", branch="staff/ad-hoc-1"
    )
    assert "curl -s http://127.0.0.1:8321/api/staff/summary" in prompt
    assert "$FLEET_API_TOKEN" in prompt
    assert prompt.endswith(workspace.FLEET_RULES)


def test_dashboard_api_note_follows_dashboard_port(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("DASHBOARD_PORT", "9000")
    assert "http://127.0.0.1:9000/api/staff/summary" in workspace.dashboard_api_note()


def test_read_only_run_prompt_also_points_at_dashboard_api() -> None:
    role = RoleSpec(name="critic", title="Critic", permissions={"code_read_only": True})
    prompt = workspace.compose_prompt(role, repo="Tools", target_ref="", operator_prompt="x", branch="b")
    assert "/api/staff/summary" in prompt
    assert prompt.endswith(workspace.READ_ONLY_FLEET_RULES)


def test_chat_turn_prompt_has_no_dashboard_api_note() -> None:
    prompt = workspace.compose_prompt(_role(), repo="", target_ref="", operator_prompt="hi", branch="", chat_turn=True)
    assert "FLEET_API_TOKEN" not in prompt


def test_both_rule_sets_end_with_the_result_contract() -> None:
    """Barb's board-secretary run (2026-09-27) did the work but dropped the STAFF_RESULT line.

    The contract used to sit mid-sentence; it is now the last sentence of both rule
    sets and says what happens without it.
    """
    for rules in (workspace.FLEET_RULES, workspace.READ_ONLY_FLEET_RULES):
        assert rules.endswith(workspace.RESULT_CONTRACT)
        assert rules.count("STAFF_RESULT:") == 1
    assert "recorded as failed" in workspace.RESULT_CONTRACT
    assert "PR URL" in workspace.FLEET_RULES
