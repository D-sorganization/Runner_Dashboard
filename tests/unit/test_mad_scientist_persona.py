"""Unit tests for porting Mad Scientist / Professor Frink to an RD persona role with dormant
Grok Bot fallback (#3704, next slice of epic #3695's cutover-tracking checklist)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import staff.mad_scientist_persona as ms
from staff.barb_continuity import BarbContinuityStore
from staff.provider_failover_config import FailoverStep, resolve_failover_chain
from staff.role_capsule import RoleCapsuleStore
from staff.role_health import RoleHealthStore
from staff.roles import clear_roles_cache, load_roles
from staff.validator import validate_role_data

from scripts import rd_barb_provider as rp

pytestmark = pytest.mark.unit
LA = ZoneInfo("America/Los_Angeles")


def test_mad_scientist_yaml_passes_schema_validation_and_loads_into_roster(tmp_path: Path) -> None:
    assert ms.write_mad_scientist_role_yaml(tmp_path).name == "mad-scientist.yml"
    spec = ms.load_mad_scientist_role_spec(tmp_path)
    assert spec.valid, spec.errors
    assert validate_role_data(spec_raw := ms.load_mad_scientist_role_spec(tmp_path, raw=True)) == []
    assert spec_raw["permissions"]["lease"] is False
    assert spec_raw["permissions"]["push_branch"] is True
    assert spec_raw["permissions"]["open_pr"] is True
    assert spec_raw["permissions"]["merge"] is False
    assert spec_raw["window"] is None
    assert spec_raw["reports_to"] == "user" and spec_raw["surface"] == "dashboard"
    assert spec_raw["providers"] == ["claude", "codex", "antigravity"]
    clear_roles_cache()
    role = load_roles(tmp_path)["mad-scientist"]
    assert role.valid
    assert role.scope["schedules"] == dict(ms.MAD_SCIENTIST_SCHEDULE_ENTRIES)
    slots = ms.next_mad_scientist_slots(datetime(2026, 11, 2, 7, 30, tzinfo=LA), role)
    assert slots["weekly_experiment"].startswith("2026-11-03T22:00:00")


def test_mad_scientist_resolved_chain_has_no_model_override(tmp_path: Path) -> None:
    ms.write_mad_scientist_role_yaml(tmp_path)
    resolved = resolve_failover_chain(ms.load_mad_scientist_role_spec(tmp_path))
    assert resolved == ms.MAD_SCIENTIST_FAILOVER_CHAIN
    assert resolved[0] == FailoverStep("claude", None)
    assert [s.provider for s in resolved] == ["claude", "codex", "antigravity"]
    # rd_barb_provider.chain() defaults a bare claude head to "opus" when nothing overrides it
    # (shared DEFAULT_PROVIDER/DEFAULT_MODEL rule, not something this role's own chain sets).
    assert rp.select({}, role="mad-scientist") == rp.Decision("claude", "opus", False)
    assert rp.chain({}, role="mad-scientist") == (
        FailoverStep("claude", "opus"),
        *ms.MAD_SCIENTIST_FAILOVER_CHAIN[1:],
    )
    sent: list[dict[str, Any]] = []
    rc = rp.main(
        ["--role", "mad-scientist", "next", "--current", "claude", "--failure", "rate_limited"],
        config={},
        notify=sent.append,
    )
    assert rc == 0 and len(sent) == 1
    assert sent[0]["sender_role"] == "mad-scientist" and "codex" in sent[0]["summary"]


def test_mad_scientist_capsule_schema_validation_and_persistence(tmp_path: Path) -> None:
    capsule_store = RoleCapsuleStore(tmp_path / "capsules")
    payload = ms.build_mad_scientist_capsule(
        last_experiment_date="2026-11-03",
        open_experiments=[{"id": "experiment-1", "summary": "Fleet seismograph", "link": "https://example.com/lab/1"}],
        next_slots=ms.next_mad_scientist_slots(datetime(2026, 11, 3, 22, 5, tzinfo=LA)),
    )
    assert ms.validate_mad_scientist_capsule(payload) == []
    saved = ms.save_mad_scientist_capsule(capsule_store, payload, epoch=1, now=1700000000.0)
    assert saved["role"] == "mad-scientist"
    assert (tmp_path / "capsules" / "barbara-mirror" / "capsules" / "mad-scientist.json").is_file()
    bad_date = {**payload, "last_scorecard_date": "bad"}
    assert any("last_scorecard_date" in e for e in ms.validate_mad_scientist_capsule(bad_date))
    bad_experiments = {**payload, "open_experiments": [123]}
    assert any("open_experiments" in e for e in ms.validate_mad_scientist_capsule(bad_experiments))
    bad_slots = {**payload, "next_slots": {}}
    assert any("next_slots" in e for e in ms.validate_mad_scientist_capsule(bad_slots))


def test_shadow_run_posts_to_rd_thread_and_suppresses_webhook(tmp_path: Path) -> None:
    webhook_calls: list[Any] = []
    thread_posts: list[tuple[str, str, str]] = []
    cfg = {
        "RD_MAD_SCIENTIST_SHADOW": "1",
        "RD_MAD_SCIENTIST_THREAD_ID": "th_mad_scientist_shadow",
        "RD_BARB_WEBHOOK_URL": "https://hook.example/mad-scientist",
        "RD_BARB_WEBHOOK_KEY": "k",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "mad-scientist-notify.json",
        "sender": lambda *a: webhook_calls.append(a),
        "thread_poster": lambda tid, body, key: thread_posts.append((tid, body, key)),
    }
    res = ms.run_mad_scientist_slot(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        job="weekly_experiment",
        slot="2026-11-03T22:00",
        experiment_date="2026-11-03",
        notify_opts=opts,
        summary="Mad Scientist weekly experiment (shadow)",
        body="1 lab experiment opened; no blocking issues.",
        open_experiments=[{"id": "experiment-1", "summary": "Fleet seismograph", "link": "https://example.com/lab/1"}],
        now=datetime(2026, 11, 3, 22, 0, tzinfo=LA),
    )
    assert res["status"] == "executed" and res["notify"] == {"ok": True, "status": "shadow"}
    assert webhook_calls == [] and len(thread_posts) == 1 and thread_posts[0][0] == "th_mad_scientist_shadow"


def test_staging_fallback_drill_and_slot_skip_after_fallback_ledger_entry(tmp_path: Path) -> None:
    webhook_calls: list[dict[str, Any]] = []
    cfg = {
        "RD_BARB_WEBHOOK_URL": "https://hook.example/mad-scientist",
        "RD_BARB_WEBHOOK_KEY": "k",
        "RD_MAD_SCIENTIST_THREAD_ID": "th_mad_scientist",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "mad-scientist-drill-notify.json",
        "sender": lambda _url, _hdrs, msg: webhook_calls.append(msg),
    }
    drill = ms.run_mad_scientist_fallback_drill(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        RoleHealthStore(tmp_path / "role-health.json"),
        slot="2026-11-03T22:00",
        experiment_date="2026-11-03",
        notify_opts=opts,
        base_time=datetime(2026, 11, 3, 21, 50, tzinfo=LA),
    )
    assert drill["takeover_notes"] == 1 and drill["back_notes"] == 1
    assert drill["rd_slot_after_resume"] == {
        "status": "skipped",
        "reason": "slot_already_executed",
        "slot_key": "mad-scientist/weekly_experiment/2026-11-03T22:00",
    }
    assert drill["final_capsule"]["epoch"] == 2
    assert any("Mad Scientist took over on Grok Bot" in m["summary"] for m in webhook_calls)
    assert any("Mad Scientist is back on RD" in m["summary"] for m in webhook_calls)
