"""Unit tests for porting Research Scout to an RD persona role with dormant Grok Bot fallback
(#3704, slice 1 of epic #3695's cutover-tracking checklist)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import staff.research_scout_persona as rs
from staff.barb_continuity import BarbContinuityStore
from staff.provider_failover_config import FailoverStep, resolve_failover_chain
from staff.role_capsule import RoleCapsuleStore
from staff.role_health import RoleHealthStore
from staff.roles import clear_roles_cache, load_roles
from staff.validator import validate_role_data

from scripts import rd_barb_provider as rp

pytestmark = pytest.mark.unit
LA = ZoneInfo("America/Los_Angeles")


def test_research_scout_yaml_passes_schema_validation_and_loads_into_roster(tmp_path: Path) -> None:
    assert rs.write_research_scout_role_yaml(tmp_path).name == "research-scout.yml"
    spec = rs.load_research_scout_role_spec(tmp_path)
    assert spec.valid, spec.errors
    assert validate_role_data(spec_raw := rs.load_research_scout_role_spec(tmp_path, raw=True)) == []
    assert spec_raw["permissions"]["code_read_only"] is True
    assert not any(spec_raw["permissions"][k] for k in ("open_pr", "merge", "lease", "notify_user"))
    assert spec_raw["reports_to"] == "barb" and spec_raw["surface"] == "both"
    assert spec_raw["providers"] == ["claude", "antigravity", "cursor-agent"]
    clear_roles_cache()
    scout = load_roles(tmp_path)["research-scout"]
    assert scout.valid and scout.code_read_only is True
    assert scout.scope["schedules"] == dict(rs.RESEARCH_SCOUT_SCHEDULE_ENTRIES)
    slots = rs.next_research_scout_slots(datetime(2026, 11, 2, 7, 30, tzinfo=LA), scout)
    assert slots["weekly_scan"].startswith("2026-11-06T08:00:00")


def test_research_scout_resolved_chain_has_no_codex_step_and_no_model_override(tmp_path: Path) -> None:
    rs.write_research_scout_role_yaml(tmp_path)
    resolved = resolve_failover_chain(rs.load_research_scout_role_spec(tmp_path))
    assert resolved == rs.RESEARCH_SCOUT_FAILOVER_CHAIN
    assert resolved[0] == FailoverStep("claude", None)
    assert all(step.provider != "codex" for step in resolved)
    # rd_barb_provider.chain() defaults a bare claude head to "opus" when nothing overrides it
    # (shared DEFAULT_PROVIDER/DEFAULT_MODEL rule, not something this role's own chain sets).
    assert rp.select({}, role="research-scout") == rp.Decision("claude", "opus", False)
    assert rp.chain({}, role="research-scout") == (
        FailoverStep("claude", "opus"),
        *rs.RESEARCH_SCOUT_FAILOVER_CHAIN[1:],
    )
    sent: list[dict[str, Any]] = []
    rc = rp.main(
        ["--role", "research-scout", "next", "--current", "claude", "--failure", "rate_limited"],
        config={},
        notify=sent.append,
    )
    assert rc == 0 and len(sent) == 1
    assert sent[0]["sender_role"] == "research-scout" and "antigravity" in sent[0]["summary"]


def test_research_scout_capsule_schema_validation_and_persistence(tmp_path: Path) -> None:
    capsule_store = RoleCapsuleStore(tmp_path / "capsules")
    proposals = [{"id": "proposal-5550", "summary": "Reversible RL tracer-bullet", "link": "https://example.com/5550"}]
    payload = rs.build_research_scout_capsule(
        last_scan_date="2026-11-06",
        submitted_proposals=proposals,
        next_slots=rs.next_research_scout_slots(datetime(2026, 11, 6, 8, 5, tzinfo=LA)),
    )
    assert rs.validate_research_scout_capsule(payload) == []
    saved = rs.save_research_scout_capsule(capsule_store, payload, epoch=1, now=1700000000.0)
    assert saved["role"] == "research-scout"
    assert (tmp_path / "capsules" / "barbara-mirror" / "capsules" / "research-scout.json").is_file()
    bad_date = {**payload, "last_scorecard_date": "bad"}
    assert any("last_scorecard_date" in e for e in rs.validate_research_scout_capsule(bad_date))
    bad_proposals = {**payload, "submitted_proposals": [{}]}
    assert any("submitted_proposals" in e for e in rs.validate_research_scout_capsule(bad_proposals))
    over_cap = {**payload, "submitted_proposals": proposals * (rs.PROPOSALS_PER_RUN + 1)}
    assert any("exceed" in e for e in rs.validate_research_scout_capsule(over_cap))
    bad_slots = {**payload, "next_slots": {}}
    assert any("next_slots" in e for e in rs.validate_research_scout_capsule(bad_slots))


def test_shadow_run_posts_to_rd_thread_and_suppresses_webhook(tmp_path: Path) -> None:
    webhook_calls: list[Any] = []
    thread_posts: list[tuple[str, str, str]] = []
    cfg = {
        "RD_RESEARCH_SCOUT_SHADOW": "1",
        "RD_RESEARCH_SCOUT_THREAD_ID": "th_research_scout_shadow",
        "RD_BARB_WEBHOOK_URL": "https://hook.example/research-scout",
        "RD_BARB_WEBHOOK_KEY": "k",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "research-scout-notify.json",
        "sender": lambda *a: webhook_calls.append(a),
        "thread_poster": lambda tid, body, key: thread_posts.append((tid, body, key)),
    }
    res = rs.run_research_scout_slot(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        job="weekly_scan",
        slot="2026-11-06T08:00",
        scan_date="2026-11-06",
        notify_opts=opts,
        summary="Research Scout weekly scan (shadow)",
        body="3 proposals drafted; 1 submitted to Board.",
        proposals=[{"id": "proposal-1", "summary": "Reversible RL tracer-bullet", "link": "https://example.com/3704"}],
        now=datetime(2026, 11, 6, 8, 0, tzinfo=LA),
    )
    assert res["status"] == "executed" and res["notify"] == {"ok": True, "status": "shadow"}
    assert webhook_calls == [] and len(thread_posts) == 1 and thread_posts[0][0] == "th_research_scout_shadow"


def test_staging_fallback_drill_and_slot_skip_after_fallback_ledger_entry(tmp_path: Path) -> None:
    webhook_calls: list[dict[str, Any]] = []
    cfg = {
        "RD_BARB_WEBHOOK_URL": "https://hook.example/research-scout",
        "RD_BARB_WEBHOOK_KEY": "k",
        "RD_RESEARCH_SCOUT_THREAD_ID": "th_research_scout",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "research-scout-drill-notify.json",
        "sender": lambda _url, _hdrs, msg: webhook_calls.append(msg),
    }
    drill = rs.run_research_scout_fallback_drill(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        RoleHealthStore(tmp_path / "role-health.json"),
        slot="2026-11-06T08:00",
        scan_date="2026-11-06",
        notify_opts=opts,
        base_time=datetime(2026, 11, 6, 7, 50, tzinfo=LA),
    )
    assert drill["takeover_notes"] == 1 and drill["back_notes"] == 1
    assert drill["rd_slot_after_resume"] == {
        "status": "skipped",
        "reason": "slot_already_executed",
        "slot_key": "research-scout/weekly_scan/2026-11-06T08:00",
    }
    assert drill["final_capsule"]["epoch"] == 2
    assert any("Research Scout took over on Grok Bot" in m["summary"] for m in webhook_calls)
    assert any("Research Scout is back on RD" in m["summary"] for m in webhook_calls)
