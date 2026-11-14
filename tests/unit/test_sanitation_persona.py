"""Unit tests for porting Sanitation Engineer to an RD persona role with dormant Grok Bot
fallback (#3704, next slice of epic #3695's cutover-tracking checklist)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import staff.sanitation_persona as sp
from staff.barb_continuity import BarbContinuityStore
from staff.provider_failover_config import FailoverStep
from staff.role_capsule import RoleCapsuleStore
from staff.role_health import RoleHealthStore
from staff.roles import clear_roles_cache, load_roles
from staff.validator import validate_role_data

from scripts import rd_barb_provider as rp

pytestmark = pytest.mark.unit
LA = ZoneInfo("America/Los_Angeles")


def test_sanitation_yaml_passes_schema_validation_and_loads_into_roster(tmp_path: Path) -> None:
    assert sp.write_sanitation_role_yaml(tmp_path).name == "sanitation.yml"
    spec = sp.load_sanitation_role_spec(tmp_path)
    assert spec.valid, spec.errors
    assert validate_role_data(spec_raw := sp.load_sanitation_role_spec(tmp_path, raw=True)) == []
    assert spec_raw["permissions"]["lease"] is True
    assert spec_raw["permissions"]["push_branch"] is True
    assert spec_raw["permissions"]["open_pr"] is True
    assert spec_raw["permissions"]["merge"] is False
    assert spec_raw["window"] == {"start": "22:00", "end": "06:00"}
    assert spec_raw["reports_to"] == "night-watch" and spec_raw["surface"] == "dashboard"
    assert spec_raw["providers"] == ["codex", "claude", "cursor-agent"]
    assert "failover_chain" in spec_raw["retry"]
    clear_roles_cache()
    role = load_roles(tmp_path)["sanitation"]
    assert role.valid
    assert role.scope["schedules"] == dict(sp.SANITATION_SCHEDULE_ENTRIES)
    assert role.scope["targets"] == list(sp.SANITATION_TARGETS)
    assert role.scope["never_delete"] == list(sp.SANITATION_NEVER_DELETE)
    assert role.scope["mode"] == "fail-closed"
    assert role.repos == sp.SANITATION_REPOS
    slots = sp.next_sanitation_slots(datetime(2026, 11, 2, 7, 30, tzinfo=LA), role)
    assert slots["overnight_sweep"].startswith("2026-11-03T03:00:00")


def test_sanitation_rd_barb_provider_chain_has_no_cursor_agent_step() -> None:
    # The real staff/roles/sanitation.yml has no retry block, so provider selection for
    # rd-barb (the one adapter that decides "which CLI runs next") relies on the static
    # PERSONA_ROLE_CHAINS entry, matching the real file's 3-provider list (no cursor-agent).
    # rd_barb_provider.chain() defaults a bare claude head to "opus" when nothing overrides it.
    assert rp.select({}, role="sanitation") == rp.Decision("codex", "gpt-6.1-sol", False)
    assert rp.chain({}, role="sanitation") == sp.SANITATION_FAILOVER_CHAIN
    sent: list[dict[str, Any]] = []
    args = ["--role", "sanitation", "next", "--current", "codex", "--failure", "rate_limited"]
    rc = rp.main(args, config={}, notify=sent.append)
    assert rc == 0 and len(sent) == 1
    assert sent[0]["sender_role"] == "sanitation" and "claude" in sent[0]["summary"]


def test_sanitation_capsule_schema_validation_and_persistence(tmp_path: Path) -> None:
    capsule_store = RoleCapsuleStore(tmp_path / "capsules")
    item = {
        "id": "sweep-1",
        "decision": "removed",
        "summary": "Removed merged worktree wt-3704-visionquest",
        "link": "https://example.com/pr/3768",
    }
    payload = sp.build_sanitation_capsule(
        last_sweep_date="2026-11-03",
        swept_items=[item],
        next_slots=sp.next_sanitation_slots(datetime(2026, 11, 3, 3, 5, tzinfo=LA)),
    )
    assert sp.validate_sanitation_capsule(payload) == []
    saved = sp.save_sanitation_capsule(capsule_store, payload, epoch=1, now=1700000000.0)
    assert saved["role"] == "sanitation"
    assert (tmp_path / "capsules" / "barbara-mirror" / "capsules" / "sanitation.json").is_file()
    bad_date = sp.validate_sanitation_capsule({**payload, "last_scorecard_date": "bad"})
    assert any("last_scorecard_date" in e for e in bad_date)
    bad_items = sp.validate_sanitation_capsule({**payload, "swept_items": [123]})
    assert any("swept_items" in e for e in bad_items)
    bad_decision = {**payload, "swept_items": [{**payload["swept_items"][0], "decision": "not-a-decision"}]}
    assert any("decision" in e for e in sp.validate_sanitation_capsule(bad_decision))
    assert any("next_slots" in e for e in sp.validate_sanitation_capsule({**payload, "next_slots": {}}))


def test_shadow_run_posts_to_rd_thread_and_suppresses_webhook(tmp_path: Path) -> None:
    webhook_calls: list[Any] = []
    thread_posts: list[tuple[str, str, str]] = []
    cfg = {
        "RD_SANITATION_SHADOW": "1",
        "RD_SANITATION_THREAD_ID": "th_sanitation_shadow",
        "RD_BARB_WEBHOOK_URL": "https://hook.example/sanitation",
        "RD_BARB_WEBHOOK_KEY": "k",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "sanitation-notify.json",
        "sender": lambda *a: webhook_calls.append(a),
        "thread_poster": lambda tid, body, key: thread_posts.append((tid, body, key)),
    }
    res = sp.run_sanitation_slot(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        job="overnight_sweep",
        slot="2026-11-03T03:00",
        sweep_date="2026-11-03",
        notify_opts=opts,
        summary="Sanitation overnight sweep (shadow)",
        body="Removed 1 stale worktree; kept everything else.",
        swept_items=[
            {
                "id": "sweep-1",
                "decision": "removed",
                "summary": "Removed merged worktree wt-3704-visionquest",
                "link": "https://example.com/pr/3768",
            }
        ],
        now=datetime(2026, 11, 3, 3, 0, tzinfo=LA),
    )
    assert res["status"] == "executed" and res["notify"] == {"ok": True, "status": "shadow"}
    assert webhook_calls == [] and len(thread_posts) == 1 and thread_posts[0][0] == "th_sanitation_shadow"


def test_staging_fallback_drill_and_slot_skip_after_fallback_ledger_entry(tmp_path: Path) -> None:
    webhook_calls: list[dict[str, Any]] = []
    cfg = {
        "RD_BARB_WEBHOOK_URL": "https://hook.example/sanitation",
        "RD_BARB_WEBHOOK_KEY": "k",
        "RD_SANITATION_THREAD_ID": "th_sanitation",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "sanitation-drill-notify.json",
        "sender": lambda _url, _hdrs, msg: webhook_calls.append(msg),
    }
    drill = sp.run_sanitation_fallback_drill(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        RoleHealthStore(tmp_path / "role-health.json"),
        slot="2026-11-03T03:00",
        sweep_date="2026-11-03",
        notify_opts=opts,
        base_time=datetime(2026, 11, 3, 3, 50, tzinfo=LA),
    )
    assert drill["takeover_notes"] == 1 and drill["back_notes"] == 1
    assert drill["rd_slot_after_resume"] == {
        "status": "skipped",
        "reason": "slot_already_executed",
        "slot_key": "sanitation/overnight_sweep/2026-11-03T03:00",
    }
    assert drill["final_capsule"]["epoch"] == 2
    assert any("Sanitation took over on Grok Bot" in m["summary"] for m in webhook_calls)
    assert any("Sanitation is back on RD" in m["summary"] for m in webhook_calls)
