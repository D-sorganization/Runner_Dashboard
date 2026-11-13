"""Unit tests for porting Fleet Critic to an RD persona role with dormant Grok Bot fallback
(#3704, next slice of epic #3695's cutover-tracking checklist)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import staff.fleet_critic_persona as fc
from staff.barb_continuity import BarbContinuityStore
from staff.provider_failover_config import FailoverStep, resolve_failover_chain
from staff.role_capsule import RoleCapsuleStore
from staff.role_health import RoleHealthStore
from staff.roles import clear_roles_cache, load_roles
from staff.validator import validate_role_data

from scripts import rd_barb_provider as rp

pytestmark = pytest.mark.unit
LA = ZoneInfo("America/Los_Angeles")


def test_fleet_critic_yaml_passes_schema_validation_and_loads_into_roster(tmp_path: Path) -> None:
    assert fc.write_fleet_critic_role_yaml(tmp_path).name == "fleet-critic.yml"
    spec = fc.load_fleet_critic_role_spec(tmp_path)
    assert spec.valid, spec.errors
    assert validate_role_data(spec_raw := fc.load_fleet_critic_role_spec(tmp_path, raw=True)) == []
    assert spec_raw["permissions"]["lease"] is True
    assert spec_raw["permissions"]["push_branch"] is True
    assert spec_raw["permissions"]["open_pr"] is True
    assert spec_raw["permissions"]["merge"] is False
    assert spec_raw["window"] is None
    assert spec_raw["reports_to"] == "barb" and spec_raw["surface"] == "both"
    assert spec_raw["providers"] == ["claude", "codex", "antigravity", "cursor-agent"]
    clear_roles_cache()
    role = load_roles(tmp_path)["fleet-critic"]
    assert role.valid
    assert role.scope["schedules"] == dict(fc.FLEET_CRITIC_SCHEDULE_ENTRIES)
    slots = fc.next_fleet_critic_slots(datetime(2026, 11, 2, 7, 30, tzinfo=LA), role)
    assert slots["weekly_critic_report"].startswith("2026-11-06T12:00:00")


def test_fleet_critic_resolved_chain_has_no_model_override(tmp_path: Path) -> None:
    fc.write_fleet_critic_role_yaml(tmp_path)
    resolved = resolve_failover_chain(fc.load_fleet_critic_role_spec(tmp_path))
    assert resolved == fc.FLEET_CRITIC_FAILOVER_CHAIN == (FailoverStep("claude", None), *resolved[1:])
    assert [s.provider for s in resolved] == ["claude", "codex", "antigravity", "cursor-agent"]
    # rd_barb_provider.chain() defaults a bare claude head to "opus" when nothing overrides it.
    assert rp.select({}, role="fleet-critic") == rp.Decision("claude", "opus", False)
    tail = fc.FLEET_CRITIC_FAILOVER_CHAIN[1:]
    assert rp.chain({}, role="fleet-critic") == (FailoverStep("claude", "opus"), *tail)
    sent: list[dict[str, Any]] = []
    args = ["--role", "fleet-critic", "next", "--current", "claude", "--failure", "rate_limited"]
    rc = rp.main(args, config={}, notify=sent.append)
    assert rc == 0 and len(sent) == 1
    assert sent[0]["sender_role"] == "fleet-critic" and "codex" in sent[0]["summary"]


def test_fleet_critic_capsule_schema_validation_and_persistence(tmp_path: Path) -> None:
    capsule_store = RoleCapsuleStore(tmp_path / "capsules")
    report = {
        "repo": "UpstreamDrift",
        "severity": "major",
        "summary": "unstated assumption",
        "link": "https://example.com/1",
    }
    payload = fc.build_fleet_critic_capsule(
        report_date="2026-11-06",
        critic_reports=[report],
        next_slots=fc.next_fleet_critic_slots(datetime(2026, 11, 6, 12, 5, tzinfo=LA)),
    )
    assert fc.validate_fleet_critic_capsule(payload) == []
    saved = fc.save_fleet_critic_capsule(capsule_store, payload, epoch=1, now=1700000000.0)
    assert saved["role"] == "fleet-critic"
    assert (tmp_path / "capsules" / "barbara-mirror" / "capsules" / "fleet-critic.json").is_file()
    bad_date = {**payload, "last_scorecard_date": "bad"}
    assert any("last_scorecard_date" in e for e in fc.validate_fleet_critic_capsule(bad_date))
    bad_repo = {**payload, "critic_reports": [{**report, "repo": "NotARepo"}]}
    assert any("repo" in e for e in fc.validate_fleet_critic_capsule(bad_repo))
    bad_severity = {**payload, "critic_reports": [{**report, "severity": "catastrophic"}]}
    assert any("severity" in e for e in fc.validate_fleet_critic_capsule(bad_severity))
    assert any("next_slots" in e for e in fc.validate_fleet_critic_capsule({**payload, "next_slots": {}}))


def test_shadow_run_posts_to_rd_thread_and_suppresses_webhook(tmp_path: Path) -> None:
    webhook_calls: list[Any] = []
    thread_posts: list[tuple[str, str, str]] = []
    cfg = {
        "RD_FLEET_CRITIC_SHADOW": "1",
        "RD_FLEET_CRITIC_THREAD_ID": "th_fleet_critic_shadow",
        "RD_BARB_WEBHOOK_URL": "https://hook.example/fleet-critic",
        "RD_BARB_WEBHOOK_KEY": "k",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "fleet-critic-notify.json",
        "sender": lambda *a: webhook_calls.append(a),
        "thread_poster": lambda tid, body, key: thread_posts.append((tid, body, key)),
    }
    res = fc.run_fleet_critic_slot(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        job="weekly_critic_report",
        slot="2026-11-06T12:00",
        report_date="2026-11-06",
        notify_opts=opts,
        summary="Fleet Critic weekly report (shadow)",
        body="1 major finding on UpstreamDrift.",
        critic_reports=[
            {"repo": "UpstreamDrift", "severity": "major", "summary": "weak evidence", "link": "https://example.com/2"}
        ],
        now=datetime(2026, 11, 6, 12, 0, tzinfo=LA),
    )
    assert res["status"] == "executed" and res["notify"] == {"ok": True, "status": "shadow"}
    assert webhook_calls == [] and len(thread_posts) == 1 and thread_posts[0][0] == "th_fleet_critic_shadow"


def test_staging_fallback_drill_and_slot_skip_after_fallback_ledger_entry(tmp_path: Path) -> None:
    webhook_calls: list[dict[str, Any]] = []
    cfg = {
        "RD_BARB_WEBHOOK_URL": "https://hook.example/fleet-critic",
        "RD_BARB_WEBHOOK_KEY": "k",
        "RD_FLEET_CRITIC_THREAD_ID": "th_fleet_critic",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "fleet-critic-drill-notify.json",
        "sender": lambda _url, _hdrs, msg: webhook_calls.append(msg),
    }
    drill = fc.run_fleet_critic_fallback_drill(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        RoleHealthStore(tmp_path / "role-health.json"),
        slot="2026-11-06T12:00",
        report_date="2026-11-06",
        notify_opts=opts,
        base_time=datetime(2026, 11, 6, 11, 50, tzinfo=LA),
    )
    assert drill["takeover_notes"] == 1 and drill["back_notes"] == 1
    assert drill["rd_slot_after_resume"] == {
        "status": "skipped",
        "reason": "slot_already_executed",
        "slot_key": "fleet-critic/weekly_critic_report/2026-11-06T12:00",
    }
    assert drill["final_capsule"]["epoch"] == 2
    assert any("Fleet Critic took over on Grok Bot" in m["summary"] for m in webhook_calls)
    assert any("Fleet Critic is back on RD" in m["summary"] for m in webhook_calls)
