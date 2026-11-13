"""Unit tests for porting Fleet Curator to an RD persona role with dormant Grok Bot fallback
(#3704, next slice of epic #3695's cutover-tracking checklist)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import staff.fleet_curator_persona as fc
from staff.barb_continuity import BarbContinuityStore
from staff.provider_failover_config import FailoverStep
from staff.role_capsule import RoleCapsuleStore
from staff.role_health import RoleHealthStore
from staff.roles import clear_roles_cache, load_roles
from staff.validator import validate_role_data

from scripts import rd_barb_provider as rp

pytestmark = pytest.mark.unit
LA = ZoneInfo("America/Los_Angeles")


def test_fleet_curator_yaml_passes_schema_validation_and_loads_into_roster(tmp_path: Path) -> None:
    assert fc.write_fleet_curator_role_yaml(tmp_path).name == "fleet-curator.yml"
    spec = fc.load_fleet_curator_role_spec(tmp_path)
    assert spec.valid, spec.errors
    assert validate_role_data(spec_raw := fc.load_fleet_curator_role_spec(tmp_path, raw=True)) == []
    assert spec_raw["permissions"]["lease"] is True
    assert spec_raw["permissions"]["push_branch"] is True
    assert spec_raw["permissions"]["open_pr"] is True
    assert spec_raw["permissions"]["merge"] is False
    assert spec_raw["window"] is None
    assert spec_raw["reports_to"] == "barb" and spec_raw["surface"] == "dashboard"
    assert spec_raw["providers"] == ["codex", "claude", "cursor-agent"]
    assert "failover_chain" in spec_raw["retry"]
    clear_roles_cache()
    role = load_roles(tmp_path)["fleet-curator"]
    assert role.valid
    assert role.scope["schedules"] == dict(fc.FLEET_CURATOR_SCHEDULE_ENTRIES)
    assert role.scope["dispositions"] == list(fc.FLEET_CURATOR_DISPOSITIONS)
    assert role.scope["pr_kind"] == "docs-only"
    assert role.repos == fc.FLEET_CURATOR_REPOS
    slots = fc.next_fleet_curator_slots(datetime(2026, 11, 2, 7, 30, tzinfo=LA), role)
    assert slots["weekly_untracked_review"].startswith("2026-11-04T06:00:00")


def test_fleet_curator_rd_barb_provider_chain_has_no_cursor_agent_step() -> None:
    # The real staff/roles/fleet-curator.yml has no retry block, so provider selection for
    # rd-barb (the one adapter that decides "which CLI runs next") relies on the static
    # PERSONA_ROLE_CHAINS entry, matching the real file's 3-provider list (no cursor-agent).
    # rd_barb_provider.chain() defaults a bare claude head to "opus" when nothing overrides it.
    assert rp.select({}, role="fleet-curator") == rp.Decision("codex", "gpt-6.1-sol", False)
    assert rp.chain({}, role="fleet-curator") == fc.FLEET_CURATOR_FAILOVER_CHAIN
    sent: list[dict[str, Any]] = []
    args = ["--role", "fleet-curator", "next", "--current", "codex", "--failure", "rate_limited"]
    rc = rp.main(args, config={}, notify=sent.append)
    assert rc == 0 and len(sent) == 1
    assert sent[0]["sender_role"] == "fleet-curator" and "claude" in sent[0]["summary"]


def test_fleet_curator_capsule_schema_validation_and_persistence(tmp_path: Path) -> None:
    capsule_store = RoleCapsuleStore(tmp_path / "capsules")
    item = {
        "id": "item-1",
        "disposition": "track",
        "summary": "Orphaned issue #9001 belongs to charter feature #8900",
        "link": "https://example.com/issue/9001",
    }
    payload = fc.build_fleet_curator_capsule(
        last_review_date="2026-11-04",
        dispositions=[item],
        next_slots=fc.next_fleet_curator_slots(datetime(2026, 11, 4, 6, 5, tzinfo=LA)),
    )
    assert fc.validate_fleet_curator_capsule(payload) == []
    saved = fc.save_fleet_curator_capsule(capsule_store, payload, epoch=1, now=1700000000.0)
    assert saved["role"] == "fleet-curator"
    assert (tmp_path / "capsules" / "barbara-mirror" / "capsules" / "fleet-curator.json").is_file()
    bad_date = fc.validate_fleet_curator_capsule({**payload, "last_scorecard_date": "bad"})
    assert any("last_scorecard_date" in e for e in bad_date)
    bad_items = fc.validate_fleet_curator_capsule({**payload, "dispositions": [123]})
    assert any("dispositions" in e for e in bad_items)
    bad_disposition = {**payload, "dispositions": [{**payload["dispositions"][0], "disposition": "not-a-disposition"}]}
    assert any("disposition" in e for e in fc.validate_fleet_curator_capsule(bad_disposition))
    assert any("next_slots" in e for e in fc.validate_fleet_curator_capsule({**payload, "next_slots": {}}))


def test_shadow_run_posts_to_rd_thread_and_suppresses_webhook(tmp_path: Path) -> None:
    webhook_calls: list[Any] = []
    thread_posts: list[tuple[str, str, str]] = []
    cfg = {
        "RD_FLEET_CURATOR_SHADOW": "1",
        "RD_FLEET_CURATOR_THREAD_ID": "th_fleet_curator_shadow",
        "RD_BARB_WEBHOOK_URL": "https://hook.example/fleet-curator",
        "RD_BARB_WEBHOOK_KEY": "k",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "fleet-curator-notify.json",
        "sender": lambda *a: webhook_calls.append(a),
        "thread_poster": lambda tid, body, key: thread_posts.append((tid, body, key)),
    }
    res = fc.run_fleet_curator_slot(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        job="weekly_untracked_review",
        slot="2026-11-04T06:00",
        review_date="2026-11-04",
        notify_opts=opts,
        summary="Fleet Curator weekly untracked-work review (shadow)",
        body="Tracked 1 orphaned issue under its charter feature.",
        dispositions=[
            {
                "id": "item-1",
                "disposition": "track",
                "summary": "Orphaned issue #9001 belongs to charter feature #8900",
                "link": "https://example.com/issue/9001",
            }
        ],
        now=datetime(2026, 11, 4, 6, 0, tzinfo=LA),
    )
    assert res["status"] == "executed" and res["notify"] == {"ok": True, "status": "shadow"}
    assert webhook_calls == [] and len(thread_posts) == 1 and thread_posts[0][0] == "th_fleet_curator_shadow"


def test_staging_fallback_drill_and_slot_skip_after_fallback_ledger_entry(tmp_path: Path) -> None:
    webhook_calls: list[dict[str, Any]] = []
    cfg = {
        "RD_BARB_WEBHOOK_URL": "https://hook.example/fleet-curator",
        "RD_BARB_WEBHOOK_KEY": "k",
        "RD_FLEET_CURATOR_THREAD_ID": "th_fleet_curator",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "fleet-curator-drill-notify.json",
        "sender": lambda _url, _hdrs, msg: webhook_calls.append(msg),
    }
    drill = fc.run_fleet_curator_fallback_drill(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        RoleHealthStore(tmp_path / "role-health.json"),
        slot="2026-11-04T06:00",
        review_date="2026-11-04",
        notify_opts=opts,
        base_time=datetime(2026, 11, 4, 6, 0, tzinfo=LA),
    )
    assert drill["takeover_notes"] == 1 and drill["back_notes"] == 1
    assert drill["rd_slot_after_resume"] == {
        "status": "skipped",
        "reason": "slot_already_executed",
        "slot_key": "fleet-curator/weekly_untracked_review/2026-11-04T06:00",
    }
    assert drill["final_capsule"]["epoch"] == 2
    assert any("Fleet Curator took over on Grok Bot" in m["summary"] for m in webhook_calls)
    assert any("Fleet Curator is back on RD" in m["summary"] for m in webhook_calls)
