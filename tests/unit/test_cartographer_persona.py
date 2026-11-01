"""Unit tests for porting Silent Cartographer to an RD persona role with dormant Grok Bot
fallback (#3704, next slice of epic #3695's cutover-tracking checklist)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import staff.cartographer_persona as cp
from staff.barb_continuity import BarbContinuityStore
from staff.provider_failover_config import FailoverStep, resolve_failover_chain
from staff.role_capsule import RoleCapsuleStore
from staff.role_health import RoleHealthStore
from staff.roles import clear_roles_cache, load_roles
from staff.schedule import in_window
from staff.validator import validate_role_data

from scripts import rd_barb_provider as rp

pytestmark = pytest.mark.unit
LA = ZoneInfo("America/Los_Angeles")


def test_cartographer_yaml_passes_schema_validation_and_loads_into_roster(tmp_path: Path) -> None:
    assert cp.write_cartographer_role_yaml(tmp_path).name == "cartographer.yml"
    spec = cp.load_cartographer_role_spec(tmp_path)
    assert spec.valid, spec.errors
    assert validate_role_data(spec_raw := cp.load_cartographer_role_spec(tmp_path, raw=True)) == []
    assert spec_raw["permissions"]["lease"] is True
    assert spec_raw["permissions"]["push_branch"] is True
    assert spec_raw["permissions"]["open_pr"] is True
    assert spec_raw["permissions"]["merge"] is False
    assert spec_raw["window"] == dict(cp.CARTOGRAPHER_WINDOW)
    assert spec_raw["reports_to"] == "barb" and spec_raw["surface"] == "dashboard"
    assert spec_raw["providers"] == ["claude", "codex", "antigravity"]
    clear_roles_cache()
    role = load_roles(tmp_path)["cartographer"]
    assert role.valid
    assert role.scope["schedules"] == dict(cp.CARTOGRAPHER_SCHEDULE_ENTRIES)
    slots = cp.next_cartographer_slots(datetime(2026, 11, 2, 7, 30, tzinfo=LA), role)
    assert slots["weekly_map_sweep"].startswith("2026-11-08T04:00:00")
    # The real overnight window (22:00-06:00) is gated generically by staff/scheduler.py
    # via staff.schedule.in_window -- no persona-side gating code needed, just the data.
    assert in_window(role.window, datetime(2026, 11, 8, 23, 30, tzinfo=LA)) is True
    assert in_window(role.window, datetime(2026, 11, 8, 5, 0, tzinfo=LA)) is True
    assert in_window(role.window, datetime(2026, 11, 8, 12, 0, tzinfo=LA)) is False


def test_cartographer_resolved_chain_has_no_model_override(tmp_path: Path) -> None:
    cp.write_cartographer_role_yaml(tmp_path)
    resolved = resolve_failover_chain(cp.load_cartographer_role_spec(tmp_path))
    assert resolved == cp.CARTOGRAPHER_FAILOVER_CHAIN
    assert resolved[0] == FailoverStep("claude", None)
    assert [s.provider for s in resolved] == ["claude", "codex", "antigravity"]
    # rd_barb_provider.chain() defaults a bare claude head to "opus" when nothing overrides it
    # (shared DEFAULT_PROVIDER/DEFAULT_MODEL rule, not something this role's own chain sets).
    assert rp.select({}, role="cartographer") == rp.Decision("claude", "opus", False)
    assert rp.chain({}, role="cartographer") == (
        FailoverStep("claude", "opus"),
        *cp.CARTOGRAPHER_FAILOVER_CHAIN[1:],
    )
    sent: list[dict[str, Any]] = []
    rc = rp.main(
        ["--role", "cartographer", "next", "--current", "claude", "--failure", "rate_limited"],
        config={},
        notify=sent.append,
    )
    assert rc == 0 and len(sent) == 1
    assert sent[0]["sender_role"] == "cartographer" and "codex" in sent[0]["summary"]


def test_cartographer_capsule_schema_validation_and_persistence(tmp_path: Path) -> None:
    capsule_store = RoleCapsuleStore(tmp_path / "capsules")
    payload = cp.build_cartographer_capsule(
        last_sweep_date="2026-11-08",
        stale_repos=["Gasification_Model"],
        next_slots=cp.next_cartographer_slots(datetime(2026, 11, 8, 4, 5, tzinfo=LA)),
    )
    assert cp.validate_cartographer_capsule(payload) == []
    saved = cp.save_cartographer_capsule(capsule_store, payload, epoch=1, now=1700000000.0)
    assert saved["role"] == "cartographer"
    assert (tmp_path / "capsules" / "barbara-mirror" / "capsules" / "cartographer.json").is_file()
    bad_date = {**payload, "last_sweep_date": "bad"}
    assert any("last_sweep_date" in e for e in cp.validate_cartographer_capsule(bad_date))
    bad_repos = {**payload, "stale_repos": [123]}
    assert any("stale_repos" in e for e in cp.validate_cartographer_capsule(bad_repos))
    bad_slots = {**payload, "next_slots": {}}
    assert any("next_slots" in e for e in cp.validate_cartographer_capsule(bad_slots))


def test_shadow_run_posts_to_rd_thread_and_suppresses_webhook(tmp_path: Path) -> None:
    webhook_calls: list[Any] = []
    thread_posts: list[tuple[str, str, str]] = []
    cfg = {
        "RD_CARTOGRAPHER_SHADOW": "1",
        "RD_CARTOGRAPHER_THREAD_ID": "th_cartographer_shadow",
        "RD_BARB_WEBHOOK_URL": "https://hook.example/cartographer",
        "RD_BARB_WEBHOOK_KEY": "k",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "cartographer-notify.json",
        "sender": lambda *a: webhook_calls.append(a),
        "thread_poster": lambda tid, body, key: thread_posts.append((tid, body, key)),
    }
    res = cp.run_cartographer_slot(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        job="weekly_map_sweep",
        slot="2026-11-08T04:00",
        sweep_date="2026-11-08",
        notify_opts=opts,
        summary="Cartographer weekly map sweep (shadow)",
        body="1 repo flagged stale; no blocking issues.",
        stale_repos=["Gasification_Model"],
        now=datetime(2026, 11, 8, 4, 0, tzinfo=LA),
    )
    assert res["status"] == "executed" and res["notify"] == {"ok": True, "status": "shadow"}
    assert webhook_calls == [] and len(thread_posts) == 1 and thread_posts[0][0] == "th_cartographer_shadow"


def test_staging_fallback_drill_and_slot_skip_after_fallback_ledger_entry(tmp_path: Path) -> None:
    webhook_calls: list[dict[str, Any]] = []
    cfg = {
        "RD_BARB_WEBHOOK_URL": "https://hook.example/cartographer",
        "RD_BARB_WEBHOOK_KEY": "k",
        "RD_CARTOGRAPHER_THREAD_ID": "th_cartographer",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "cartographer-drill-notify.json",
        "sender": lambda _url, _hdrs, msg: webhook_calls.append(msg),
    }
    drill = cp.run_cartographer_fallback_drill(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        RoleHealthStore(tmp_path / "role-health.json"),
        slot="2026-11-08T04:00",
        sweep_date="2026-11-08",
        notify_opts=opts,
        base_time=datetime(2026, 11, 8, 3, 50, tzinfo=LA),
    )
    assert drill["takeover_notes"] == 1 and drill["back_notes"] == 1
    assert drill["rd_slot_after_resume"] == {
        "status": "skipped",
        "reason": "slot_already_executed",
        "slot_key": "cartographer/weekly_map_sweep/2026-11-08T04:00",
    }
    assert drill["final_capsule"]["epoch"] == 2
    assert any("Cartographer took over on Grok Bot" in m["summary"] for m in webhook_calls)
    assert any("Cartographer is back on RD" in m["summary"] for m in webhook_calls)
