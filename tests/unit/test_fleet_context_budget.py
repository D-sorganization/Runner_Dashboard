"""Regression and contract tests for fleet context budget, caching, and dedup (#2220)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from coordination import briefing as briefing_mod
from staff import summary_view, workspace
from staff.context_tokens import estimate_tokens
from staff.dispatch_bundle import DispatchBundle
from staff.plan import RunPlan, RunRequest
from staff.roles import RoleSpec
from staff.runner_ops import new_run_record
from staff.standards_registry import FleetStandards, load_fleet_standards
from staff.store import RunStore


def _role() -> RoleSpec:
    return RoleSpec(name="night-watch", title="Night Watch", instructions="Triage.", playbook="docs/play.md")


def test_standards_registry_dbc_and_file_loading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """DbC: load_fleet_standards returns non-empty body and semver; parses file or falls back."""
    monkeypatch.delenv("STAFF_STANDARDS_PATH", raising=False)
    monkeypatch.setenv("STAFF_RM_ROOT", "/nonexistent/rm/root")
    s_fallback = load_fleet_standards()
    assert s_fallback.version == "1.0.0" and s_fallback.source_path == "<builtin>" and s_fallback.fallback is True
    assert s_fallback.body.strip() and s_fallback.pin.startswith("fleet-standards@1.0.0+")

    body = "# Fleet Standards\n- TDD, DbC, LoD, DRY.\n"
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    f = tmp_path / "FLEET.md"
    f.write_text(f"---\nname: fleet-standards\nversion: 1.1.0\ndigest: {digest}\n---\n\n{body}", encoding="utf-8")
    monkeypatch.setenv("STAFF_STANDARDS_PATH", str(f))
    s_file = load_fleet_standards()
    assert (
        s_file.version == "1.1.0" and s_file.fallback is False and s_file.pin == f"fleet-standards@1.1.0+{digest[:12]}"
    )


@pytest.mark.asyncio
async def test_briefing_and_summary_standards_reporting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Briefing & summary report pin/source and emit warning/attention on <builtin>."""
    monkeypatch.delenv("STAFF_STANDARDS_PATH", raising=False)
    monkeypatch.setenv("STAFF_RM_ROOT", "/nonexistent/rm")
    b = await briefing_mod.build_briefing(repo="Runner_Dashboard", agent="codex")
    assert b["standards_pin"].startswith("fleet-standards@1.0.0+") and b["standards_source"] == "<builtin>"
    assert any("<builtin>" in w for w in b["warnings"]) and b["rules"]

    mr = MagicMock()
    mr.store.list_runs.return_value, mr.roles.return_value, mr.machine = [], {}, "desk"
    monkeypatch.setattr(summary_view, "get_runner", lambda: mr)
    empty = {
        "generated_at": "now",
        "hub": "h",
        "online": [],
        "offline": [],
        "running": [],
        "queued": [],
        "spend_today_usd": {},
        "providers": {},
    }

    async def _agg(x: dict) -> dict:
        return x

    monkeypatch.setattr(summary_view.staff_fleet, "local_board", lambda r: empty)
    monkeypatch.setattr(summary_view.staff_fleet, "aggregate_board", _agg)
    monkeypatch.setattr(summary_view, "run_preflight_and_cache", lambda r: {"ok": True})
    monkeypatch.setattr(summary_view, "aggregate_provider_auth", lambda b: {})
    monkeypatch.setattr(summary_view, "provider_auth_cache_snapshot", lambda: {})
    s = await summary_view.build_staff_summary()
    assert s.get("standards_source") == "<builtin>"
    assert any(item.get("id") == "fleet-standards-builtin-fallback" for item in s.get("attention", []))


def test_no_remaining_fleet_rules_literal_and_cache_ordering() -> None:
    """Acceptance: no FLEET_RULES literal in workspace.py; cache-friendly ordering."""
    workspace_src = Path(workspace.__file__).read_text(encoding="utf-8")
    assert not re.search(r"^\s*FLEET_RULES\s*=\s*\(", workspace_src, re.MULTILINE)
    assert hasattr(workspace, "FLEET_RULES") and isinstance(workspace.FLEET_RULES, str)

    role, pin = _role(), "fleet-standards@1.1.0+8838e7f99240"
    bundle = DispatchBundle(
        standards_pin=pin, skill_block="Skill block", standards_block=f"Fleet standards ({pin}):\nRules"
    )
    p1 = workspace.compose_prompt(role, repo="Tools", operator_prompt="Task 1", dispatch_bundle=bundle)
    p2 = workspace.compose_prompt(role, repo="Tools", operator_prompt="Task 2", dispatch_bundle=bundle)
    std_idx, skill_idx = p1.index(bundle.standards_block), p1.index(bundle.skill_block)
    dyn_idx, task_idx = p1.index("Repository:"), p1.index("Task from the operator:")
    assert std_idx < skill_idx < dyn_idx < task_idx and p1[:dyn_idx] == p2[:dyn_idx]


def test_dedup_pin_and_aggregate_context_budget() -> None:
    """AGENTS.md dedup pin replaces block; aggregate context is <= 2200 tokens; over-budget fails."""
    role, pin = _role(), "fleet-standards@1.1.0+8838e7f99240"
    bundle = DispatchBundle(
        standards_pin=pin, skill_block="Skill", standards_block=f"Fleet standards ({pin}):\nLong text " * 30
    )
    assert f"Fleet standards: verified in repository AGENTS.md ({pin})." in workspace.compose_prompt(
        role, repo="Tools", dispatch_bundle=bundle, repo_agents_text=f"<!-- FLEET-STANDARDS-PIN: {pin} -->"
    )
    assert "Long text" in workspace.compose_prompt(
        role, repo="Tools", dispatch_bundle=bundle, repo_agents_text="no pin"
    )

    standards = load_fleet_standards()
    assert estimate_tokens(standards.body) <= 1500
    prompt = workspace.compose_prompt(
        role, repo="Tools", target_ref="i#1", operator_prompt="Task", dispatch_bundle=bundle
    )
    assert estimate_tokens(prompt) <= 2200

    huge_std = FleetStandards("1.1.0", "Rule\n" * 1500, "h1", "f")
    bundle_huge = DispatchBundle(standards_pin=huge_std.pin, standards_block=huge_std.body)
    with pytest.raises(ValueError, match="Tier 0 token budget"):
        workspace.compose_prompt(role, dispatch_bundle=bundle_huge)


def test_run_plan_and_record_stamps_context_metadata(tmp_path: Path) -> None:
    """Run plan and record stamp standards_version, token estimate, pin, and context metadata."""
    kwargs = dict(
        role="night-watch",
        provider="codex",
        model="gpt-5",
        repo="Tools",
        target_kind="issue",
        target_ref="#1",
        operator_prompt="Triage",
        prompt="Prompt",
        argv=["codex"],
        branch="b",
        lease_ritual=True,
        standards_version="1.1.0",
        standards_pin="fleet-standards@1.1.0+8838e7f99240",
        standards_token_estimate=1460,
        context_metadata={"pack_version": "1.1.0", "aggregate_tokens": 1750},
    )
    req = RunRequest(role="night-watch", repo="Tools", issue=1)
    rec = new_run_record(RunPlan(**kwargs), req, machine="desk", max_attempts=2, run_id="run-test-01")
    assert rec.standards_version == "1.1.0" and rec.standards_token_estimate == 1460
    assert rec.to_dict()["standards"]["token_estimate"] == 1460

    store = RunStore(tmp_path / "runs.sqlite3")
    store.create_run(rec)
    loaded = store.get_run(rec.id)
    assert loaded is not None and loaded.standards_token_estimate == 1460
