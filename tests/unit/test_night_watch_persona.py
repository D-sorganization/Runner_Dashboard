"""Unit tests for porting Night Watch to an RD persona role with dormant Grok Bot fallback
(#3704, next slice of epic #3695's cutover-tracking checklist)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import staff.night_watch_persona as nw
from staff.barb_continuity import BarbContinuityStore
from staff.provider_failover_config import FailoverStep, resolve_failover_chain
from staff.role_capsule import RoleCapsuleStore
from staff.role_health import RoleHealthStore
from staff.roles import clear_roles_cache, load_roles
from staff.validator import validate_role_data

from scripts import rd_barb_provider as rp

pytestmark = pytest.mark.unit
LA = ZoneInfo("America/Los_Angeles")


def test_night_watch_yaml_passes_schema_validation_and_loads_into_roster(tmp_path: Path) -> None:
    assert nw.write_night_watch_role_yaml(tmp_path).name == "night-watch.yml"
    spec = nw.load_night_watch_role_spec(tmp_path)
    assert spec.valid, spec.errors
    assert validate_role_data(spec_raw := nw.load_night_watch_role_spec(tmp_path, raw=True)) == []
    assert spec_raw["permissions"]["lease"] is True
    assert spec_raw["permissions"]["push_branch"] is True
    assert spec_raw["permissions"]["open_pr"] is True
    assert spec_raw["permissions"]["merge"] is False
    assert spec_raw["window"] == {"start": "22:00", "end": "06:00"}
    assert spec_raw["reports_to"] == "barb" and spec_raw["surface"] == "dashboard"
    assert spec_raw["providers"] == ["claude", "codex", "antigravity"]
    clear_roles_cache()
    role = load_roles(tmp_path)["night-watch"]
    assert role.valid
    assert role.scope["schedules"] == dict(nw.NIGHT_WATCH_SCHEDULE_ENTRIES)
    assert role.scope["sub_roles"] == list(nw.NIGHT_WATCH_SUB_ROLES)
    slots = nw.next_night_watch_slots(datetime(2026, 11, 2, 7, 30, tzinfo=LA), role)
    assert slots["overnight_triage"].startswith("2026-11-02T22:00:00")


def test_night_watch_resolved_chain_has_no_model_override(tmp_path: Path) -> None:
    nw.write_night_watch_role_yaml(tmp_path)
    resolved = resolve_failover_chain(nw.load_night_watch_role_spec(tmp_path))
    assert resolved == nw.NIGHT_WATCH_FAILOVER_CHAIN == (FailoverStep("claude", None), *resolved[1:])
    assert [s.provider for s in resolved] == ["claude", "codex", "antigravity"]
    # rd_barb_provider.chain() defaults a bare claude head to "opus" when nothing overrides it.
    assert rp.select({}, role="night-watch") == rp.Decision("claude", "opus", False)
    assert rp.chain({}, role="night-watch") == (FailoverStep("claude", "opus"), *nw.NIGHT_WATCH_FAILOVER_CHAIN[1:])
    sent: list[dict[str, Any]] = []
    args = ["--role", "night-watch", "next", "--current", "claude", "--failure", "rate_limited"]
    rc = rp.main(args, config={}, notify=sent.append)
    assert rc == 0 and len(sent) == 1
    assert sent[0]["sender_role"] == "night-watch" and "codex" in sent[0]["summary"]


def test_night_watch_capsule_schema_validation_and_persistence(tmp_path: Path) -> None:
    capsule_store = RoleCapsuleStore(tmp_path / "capsules")
    item = {
        "id": "triage-1",
        "summary": "Closed 2 stale worktrees",
        "link": "https://example.com/pr/1",
        "sub_role": "sanitation",
    }
    payload = nw.build_night_watch_capsule(
        last_overnight_date="2026-11-03",
        triage_items=[item],
        next_slots=nw.next_night_watch_slots(datetime(2026, 11, 3, 22, 5, tzinfo=LA)),
    )
    assert nw.validate_night_watch_capsule(payload) == []
    saved = nw.save_night_watch_capsule(capsule_store, payload, epoch=1, now=1700000000.0)
    assert saved["role"] == "night-watch"
    assert (tmp_path / "capsules" / "barbara-mirror" / "capsules" / "night-watch.json").is_file()
    assert any(
        "last_scorecard_date" in e for e in nw.validate_night_watch_capsule({**payload, "last_scorecard_date": "bad"})
    )
    assert any("triage_items" in e for e in nw.validate_night_watch_capsule({**payload, "triage_items": [123]}))
    bad_sub_role = {**payload, "triage_items": [{**payload["triage_items"][0], "sub_role": "not-a-role"}]}
    assert any("sub_role" in e for e in nw.validate_night_watch_capsule(bad_sub_role))
    assert any("next_slots" in e for e in nw.validate_night_watch_capsule({**payload, "next_slots": {}}))


def test_shadow_run_posts_to_rd_thread_and_suppresses_webhook(tmp_path: Path) -> None:
    webhook_calls: list[Any] = []
    thread_posts: list[tuple[str, str, str]] = []
    cfg = {
        "RD_NIGHT_WATCH_SHADOW": "1",
        "RD_NIGHT_WATCH_THREAD_ID": "th_night_watch_shadow",
        "RD_BARB_WEBHOOK_URL": "https://hook.example/night-watch",
        "RD_BARB_WEBHOOK_KEY": "k",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "night-watch-notify.json",
        "sender": lambda *a: webhook_calls.append(a),
        "thread_poster": lambda tid, body, key: thread_posts.append((tid, body, key)),
    }
    res = nw.run_night_watch_slot(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        job="overnight_triage",
        slot="2026-11-03T22:00",
        overnight_date="2026-11-03",
        notify_opts=opts,
        summary="Night Watch overnight pass (shadow)",
        body="2 worktrees cleaned up; no blocking issues.",
        triage_items=[
            {
                "id": "triage-1",
                "summary": "Closed 2 stale worktrees",
                "link": "https://example.com/pr/1",
                "sub_role": "sanitation",
            }
        ],
        now=datetime(2026, 11, 3, 22, 0, tzinfo=LA),
    )
    assert res["status"] == "executed" and res["notify"] == {"ok": True, "status": "shadow"}
    assert webhook_calls == [] and len(thread_posts) == 1 and thread_posts[0][0] == "th_night_watch_shadow"


def test_staging_fallback_drill_and_slot_skip_after_fallback_ledger_entry(tmp_path: Path) -> None:
    webhook_calls: list[dict[str, Any]] = []
    cfg = {
        "RD_BARB_WEBHOOK_URL": "https://hook.example/night-watch",
        "RD_BARB_WEBHOOK_KEY": "k",
        "RD_NIGHT_WATCH_THREAD_ID": "th_night_watch",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "night-watch-drill-notify.json",
        "sender": lambda _url, _hdrs, msg: webhook_calls.append(msg),
    }
    drill = nw.run_night_watch_fallback_drill(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        RoleHealthStore(tmp_path / "role-health.json"),
        slot="2026-11-03T22:00",
        overnight_date="2026-11-03",
        notify_opts=opts,
        base_time=datetime(2026, 11, 3, 21, 50, tzinfo=LA),
    )
    assert drill["takeover_notes"] == 1 and drill["back_notes"] == 1
    assert drill["rd_slot_after_resume"] == {
        "status": "skipped",
        "reason": "slot_already_executed",
        "slot_key": "night-watch/overnight_triage/2026-11-03T22:00",
    }
    assert drill["final_capsule"]["epoch"] == 2
    assert any("Night Watch took over on Grok Bot" in m["summary"] for m in webhook_calls)
    assert any("Night Watch is back on RD" in m["summary"] for m in webhook_calls)
