"""Tests for RD #2220: Context budget caps, cache-friendly prompt ordering, and standards stamping."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from coordination.briefing import build_briefing
from staff import workspace
from staff.dispatch_bundle import resolve_dispatch_bundle
from staff.roles import RoleSpec
from staff.standards_registry import (
    ContextBudgetExceededError,
    FleetStandards,
    estimate_tokens,
    load_fleet_standards,
)
from staff.summary_view import build_staff_summary


def _prompt(**kw: Any) -> str:
    r = RoleSpec("night-watch", "Night Watch", instructions="triage")
    base = {"role": r, "repo": "Tools", "target_ref": "1", "operator_prompt": "T", "branch": "b"}
    return workspace.compose_prompt(**(base | kw))


@pytest.mark.asyncio
async def test_briefing_and_summary_standards_reporting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAFF_STANDARDS_PATH", raising=False)
    monkeypatch.setattr(workspace, "rm_root", lambda: None)
    b = await build_briefing(repo="Tools", agent="Argon")
    s = await build_staff_summary()
    assert b["standards_source"] == "<builtin>" and any("<builtin>" in w for w in b["warnings"])
    assert s["standards_source"] == "<builtin>" and s.get("standards_attention") is True

    custom = tmp_path / "FLEET.md"
    custom.write_text("---\nname: fleet-standards\nversion: 1.1.0\ndigest: a\nsource_revision: m\n---\nTDD\n")
    monkeypatch.setenv("STAFF_STANDARDS_PATH", str(custom))
    b2 = await build_briefing(repo="Tools", agent="Argon")
    assert b2["standards_source"] == str(custom) and not any("<builtin>" in w for w in b2["warnings"])


def test_compose_prompt_cache_friendly_ordering() -> None:
    bundle = resolve_dispatch_bundle(role="night-watch", repo="Tools")
    lessons = ["Lesson 101: lint", "Lesson 102: mock"]
    p1 = _prompt(target_ref="42", operator_prompt="Fix", branch="b42", dispatch_bundle=bundle, lessons=lessons)
    assert p1.startswith(bundle.standards_block) or p1.startswith("Fleet standards")
    l_idx, d_idx = p1.find("Lesson 101"), p1.find("Role instructions:")
    assert (p1.find(bundle.skill_block) < l_idx < d_idx) if bundle.skill_block else (l_idx < d_idx)
    p2 = _prompt(target_ref="99", operator_prompt="Other", branch="b99", dispatch_bundle=bundle, lessons=lessons)
    assert p1.split("Target: ")[0] == p2.split("Target: ")[0]


def test_compose_prompt_dedup_pin(tmp_path: Path) -> None:
    st, b = load_fleet_standards(), resolve_dispatch_bundle(role="night-watch", repo="Tools")
    (tmp_path / "AGENTS.md").write_text(f"pin: {st.pin}\n", encoding="utf-8")
    assert f"Fleet standards pinned: {st.pin}" in _prompt(dispatch_bundle=b, target_repo_path=tmp_path)
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "AGENTS.md").write_text("No pin\n", encoding="utf-8")
    assert st.body.strip() in _prompt(dispatch_bundle=b, target_repo_path=tmp_path / "other")


def test_compose_prompt_aggregate_budget_caps() -> None:
    st = load_fleet_standards()
    assert isinstance(st, FleetStandards) and st.body.strip() and "." in st.version and st.pin.startswith("fleet-")
    b = resolve_dispatch_bundle(role="night-watch", repo="Tools")
    assert estimate_tokens(_prompt(dispatch_bundle=b)) <= 2200
    huge = FleetStandards(version="1.0.0", body="A" * 6500, content_hash="h", source_path="h.md")
    with pytest.raises(ContextBudgetExceededError, match="Tier 0"):
        _prompt(custom_standards=huge)
    with pytest.raises(ContextBudgetExceededError, match="Lessons"):
        _prompt(dispatch_bundle=b, lessons=["L" * 2800])


def test_run_context_stamping_and_token_accounting(monkeypatch: pytest.MonkeyPatch) -> None:
    from staff.plan import RunRequest
    from staff.runner import get_runner

    monkeypatch.setattr(
        "staff.failover_dispatch.resolve_provider_and_model",
        lambda *args, **kwargs: ("claude", None, "routine", []),
    )
    rec, plan, _ = get_runner().admit(RunRequest(role="ad-hoc", repo="Tools", issue=1, prompt="Review"))
    assert plan.standards_version and plan.standards_tokens > 0
    stamp = json.loads(plan.context_stamp)
    assert stamp["pack_version"] and stamp["estimator_version"] == "chars_div_4_v1"
    assert rec.standards_version == plan.standards_version and rec.context_stamp == plan.context_stamp
