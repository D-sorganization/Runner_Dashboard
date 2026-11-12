"""Unit tests for porting PM-Sidekick to an RD persona role with dormant Grok Bot fallback (#3699)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import staff.pm_sidekick_persona as ps
from staff.barb_continuity import BarbContinuityStore
from staff.provider_failover_config import FailoverStep, resolve_failover_chain
from staff.role_capsule import RoleCapsuleStore
from staff.role_health import RoleHealthStore
from staff.roles import clear_roles_cache, load_roles
from staff.validator import validate_role_data

from scripts import rd_barb_provider as rp

pytestmark = pytest.mark.unit
LA = ZoneInfo("America/Los_Angeles")


def test_pm_sidekick_yaml_passes_schema_validation_and_loads_into_roster(tmp_path: Path) -> None:
    assert ps.write_pm_sidekick_role_yaml(tmp_path).name == "pm-sidekick.yml"
    spec = ps.load_pm_sidekick_role_spec(tmp_path)
    assert spec.valid, spec.errors
    assert validate_role_data(spec_raw := ps.load_pm_sidekick_role_spec(tmp_path, raw=True)) == []
    assert spec_raw["permissions"]["code_read_only"] is True
    assert not any(spec_raw["permissions"][k] for k in ("open_pr", "merge", "lease"))
    assert spec_raw["permissions"]["notify_user"] is True
    assert spec_raw["reports_to"] == "barb" and spec_raw["surface"] == "both"
    clear_roles_cache()
    pms = load_roles(tmp_path)["pm-sidekick"]
    assert pms.valid and pms.code_read_only is True
    assert pms.scope["schedules"] == dict(ps.PM_SIDEKICK_SCHEDULE_ENTRIES)
    slots = ps.next_pm_sidekick_slots(datetime(2026, 11, 2, 7, 30, tzinfo=LA), pms)
    assert slots["sidekick_digest"].startswith("2026-11-02T09:00:00")


def test_pm_sidekick_resolved_chain_puts_opus_5_5_first_and_override_emits_one_fyi(tmp_path: Path) -> None:
    ps.write_pm_sidekick_role_yaml(tmp_path)
    resolved = resolve_failover_chain(ps.load_pm_sidekick_role_spec(tmp_path))
    assert resolved == ps.PM_SIDEKICK_FAILOVER_CHAIN
    assert resolved[0] == FailoverStep("claude", "claude-opus-5-5")
    assert rp.select({}, role="pm-sidekick") == rp.Decision("claude", "claude-opus-5-5", False)
    assert rp.chain({}, role="pm-sidekick") == ps.PM_SIDEKICK_FAILOVER_CHAIN
    sent: list[dict[str, Any]] = []
    rc = rp.main(
        ["--role", "pm-sidekick", "next", "--current", "claude", "--failure", "rate_limited"],
        config={},
        notify=sent.append,
    )
    assert rc == 0 and len(sent) == 1
    assert sent[0]["sender_role"] == "pm-sidekick" and "antigravity" in sent[0]["summary"]


def test_pm_sidekick_capsule_schema_validation_and_persistence(tmp_path: Path) -> None:
    capsule_store = RoleCapsuleStore(tmp_path / "capsules")
    blockers = [{"id": "blocker-5551", "summary": "Fix session resume", "link": "https://example.com/5551"}]
    payload = ps.build_pm_sidekick_capsule(
        last_digest_date="2026-11-02",
        open_blockers=blockers,
        next_slots=ps.next_pm_sidekick_slots(datetime(2026, 11, 2, 9, 5, tzinfo=LA)),
    )
    assert ps.validate_pm_sidekick_capsule(payload) == []
    saved = ps.save_pm_sidekick_capsule(capsule_store, payload, epoch=1, now=1700000000.0)
    assert saved["role"] == "pm-sidekick"
    assert (tmp_path / "capsules" / "barbara-mirror" / "capsules" / "pm-sidekick.json").is_file()
    bad_date = {**payload, "last_scorecard_date": "bad"}
    assert any("last_scorecard_date" in e for e in ps.validate_pm_sidekick_capsule(bad_date))
    bad_blockers = {**payload, "open_blockers": [{}]}
    assert any("open_blockers" in e for e in ps.validate_pm_sidekick_capsule(bad_blockers))
    bad_slots = {**payload, "next_slots": {}}
    assert any("next_slots" in e for e in ps.validate_pm_sidekick_capsule(bad_slots))


def test_shadow_run_posts_to_rd_thread_and_suppresses_webhook(tmp_path: Path) -> None:
    webhook_calls: list[Any] = []
    thread_posts: list[tuple[str, str, str]] = []
    cfg = {
        "RD_PM_SIDEKICK_SHADOW": "1",
        "RD_PM_SIDEKICK_THREAD_ID": "th_pm_sidekick_shadow",
        "RD_BARB_WEBHOOK_URL": "https://hook.example/pm-sidekick",
        "RD_BARB_WEBHOOK_KEY": "k",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "pm-sidekick-notify.json",
        "sender": lambda *a: webhook_calls.append(a),
        "thread_poster": lambda tid, body, key: thread_posts.append((tid, body, key)),
    }
    res = ps.run_pm_sidekick_slot(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        job="sidekick_digest",
        slot="2026-11-02T09:00",
        digest_date="2026-11-02",
        notify_opts=opts,
        summary="PM-Sidekick weekday digest (shadow)",
        body="1 open blocker; queue depth nominal.",
        blockers=[{"id": "blocker-1", "summary": "Keep queue <= 20", "link": "https://example.com/3699"}],
        now=datetime(2026, 11, 2, 9, 0, tzinfo=LA),
    )
    assert res["status"] == "executed" and res["notify"] == {"ok": True, "status": "shadow"}
    assert webhook_calls == [] and len(thread_posts) == 1 and thread_posts[0][0] == "th_pm_sidekick_shadow"


def test_staging_fallback_drill_and_slot_skip_after_fallback_ledger_entry(tmp_path: Path) -> None:
    webhook_calls: list[dict[str, Any]] = []
    cfg = {
        "RD_BARB_WEBHOOK_URL": "https://hook.example/pm-sidekick",
        "RD_BARB_WEBHOOK_KEY": "k",
        "RD_PM_SIDEKICK_THREAD_ID": "th_pm_sidekick",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "pm-sidekick-drill-notify.json",
        "sender": lambda _url, _hdrs, msg: webhook_calls.append(msg),
    }
    drill = ps.run_pm_sidekick_fallback_drill(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        RoleHealthStore(tmp_path / "role-health.json"),
        slot="2026-11-02T09:00",
        digest_date="2026-11-02",
        notify_opts=opts,
        base_time=datetime(2026, 11, 2, 8, 50, tzinfo=LA),
    )
    assert drill["takeover_notes"] == 1 and drill["back_notes"] == 1
    assert drill["rd_slot_after_resume"] == {
        "status": "skipped",
        "reason": "slot_already_executed",
        "slot_key": "pm-sidekick/sidekick_digest/2026-11-02T09:00",
    }
    assert drill["final_capsule"]["epoch"] == 2
    assert any("PM-Sidekick took over on Grok Bot" in m["summary"] for m in webhook_calls)
    assert any("PM-Sidekick is back on RD" in m["summary"] for m in webhook_calls)
