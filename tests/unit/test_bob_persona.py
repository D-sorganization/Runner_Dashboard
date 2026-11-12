"""Unit tests for porting Bob to an RD persona role with dormant Grok Bot fallback (#3296)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import staff.bob_persona as bp
from staff.barb_continuity import BarbContinuityStore
from staff.provider_failover_config import FailoverStep, resolve_failover_chain
from staff.role_capsule import RoleCapsuleStore
from staff.role_health import RoleHealthStore
from staff.roles import clear_roles_cache, load_roles
from staff.validator import validate_role_data

from scripts import rd_barb_provider as rp

pytestmark = pytest.mark.unit
LA = ZoneInfo("America/Los_Angeles")


def test_bob_yaml_passes_schema_validation_and_loads_into_roster(tmp_path: Path) -> None:
    assert bp.write_bob_role_yaml(tmp_path).name == "bob.yml"
    spec = bp.load_bob_role_spec(tmp_path)
    assert spec.valid, spec.errors
    assert validate_role_data(spec_raw := bp.load_bob_role_spec(tmp_path, raw=True)) == []
    assert spec_raw["permissions"]["code_read_only"] is True
    assert not any(spec_raw["permissions"][k] for k in ("open_pr", "merge", "lease"))
    assert spec_raw["permissions"]["notify_user"] is True
    assert spec_raw["reports_to"] == "barb" and spec_raw["surface"] == "both"
    clear_roles_cache()
    bob = load_roles(tmp_path)["bob"]
    assert bob.valid and bob.code_read_only is True and bob.scope["schedules"] == dict(bp.BOB_SCHEDULE_ENTRIES)
    slots = bp.next_bob_slots(datetime(2026, 10, 28, 9, 30, tzinfo=LA), bob)
    assert slots["wave_scoring"].startswith("2026-10-28T10:00:00")
    assert slots["daily_note"].startswith("2026-10-28T17:00:00")


def test_bob_resolved_chain_puts_opus_5_5_first_and_override_emits_one_fyi(tmp_path: Path) -> None:
    bp.write_bob_role_yaml(tmp_path)
    resolved = resolve_failover_chain(bp.load_bob_role_spec(tmp_path))
    assert resolved == bp.BOB_FAILOVER_CHAIN and resolved[0] == FailoverStep("claude", "claude-opus-5-5")
    assert rp.select({}, role="bob") == rp.Decision("claude", "claude-opus-5-5", False)
    assert rp.chain({}, role="bob") == bp.BOB_FAILOVER_CHAIN
    sent: list[dict[str, Any]] = []
    rc = rp.main(
        ["--role", "bob", "next", "--current", "claude", "--failure", "rate_limited"], config={}, notify=sent.append
    )
    assert rc == 0 and len(sent) == 1 and sent[0]["sender_role"] == "bob" and "antigravity" in sent[0]["summary"]


def test_bob_capsule_schema_validation_and_persistence(tmp_path: Path) -> None:
    capsule_store = RoleCapsuleStore(tmp_path / "capsules")
    recs = [{"id": "rec-3450", "summary": "Require checks", "link": "https://example.com/3450"}]
    payload = bp.build_bob_capsule(
        last_scorecard_date="2026-10-28",
        open_recommendations=recs,
        next_slots=bp.next_bob_slots(datetime(2026, 10, 28, 17, 5, tzinfo=LA)),
    )
    assert bp.validate_bob_capsule(payload) == []
    saved = bp.save_bob_capsule(capsule_store, payload, epoch=1, now=1700000000.0)
    assert saved["role"] == "bob" and (tmp_path / "capsules" / "barbara-mirror" / "capsules" / "bob.json").is_file()
    assert any("last_scorecard_date" in e for e in bp.validate_bob_capsule({**payload, "last_scorecard_date": "bad"}))
    assert any("open_recommendations" in e for e in bp.validate_bob_capsule({**payload, "open_recommendations": [{}]}))
    assert any("next_slots" in e for e in bp.validate_bob_capsule({**payload, "next_slots": {}}))


def test_shadow_run_posts_to_rd_thread_and_suppresses_webhook(tmp_path: Path) -> None:
    webhook_calls: list[Any] = []
    thread_posts: list[tuple[str, str, str]] = []
    cfg = {
        "RD_BOB_SHADOW": "1",
        "RD_BOB_THREAD_ID": "th_bob_shadow",
        "RD_BARB_WEBHOOK_URL": "https://hook.example/bob",
        "RD_BARB_WEBHOOK_KEY": "k",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "bob-notify.json",
        "sender": lambda *a: webhook_calls.append(a),
        "thread_poster": lambda tid, body, key: thread_posts.append((tid, body, key)),
    }
    res = bp.run_bob_slot(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        job="daily_note",
        slot="2026-10-28T17:00",
        scorecard_date="2026-10-28",
        notify_opts=opts,
        summary="Bob 5 PM daily note (shadow)",
        body="Wave score 94%; 0 duplicate slots.",
        recommendations=[{"id": "rec-1", "summary": "Keep queue <= 6", "link": "https://example.com/3296"}],
        now=datetime(2026, 10, 28, 17, 0, tzinfo=LA),
    )
    assert res["status"] == "executed" and res["notify"] == {"ok": True, "status": "shadow"}
    assert webhook_calls == [] and len(thread_posts) == 1 and thread_posts[0][0] == "th_bob_shadow"


def test_staging_fallback_drill_and_slot_skip_after_fallback_ledger_entry(tmp_path: Path) -> None:
    webhook_calls: list[dict[str, Any]] = []
    cfg = {"RD_BARB_WEBHOOK_URL": "https://hook.example/bob", "RD_BARB_WEBHOOK_KEY": "k", "RD_BOB_THREAD_ID": "th_bob"}
    opts = {
        "config": cfg,
        "state_path": tmp_path / "bob-drill-notify.json",
        "sender": lambda _url, _hdrs, msg: webhook_calls.append(msg),
    }
    drill = bp.run_bob_fallback_drill(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        RoleHealthStore(tmp_path / "role-health.json"),
        slot="2026-10-28T17:00",
        scorecard_date="2026-10-28",
        notify_opts=opts,
        base_time=datetime(2026, 10, 28, 16, 40, tzinfo=LA),
    )
    assert drill["takeover_notes"] == 1 and drill["back_notes"] == 1
    assert drill["rd_slot_after_resume"] == {
        "status": "skipped",
        "reason": "slot_already_executed",
        "slot_key": "bob/daily_note/2026-10-28T17:00",
    }
    assert drill["final_capsule"]["epoch"] == 2
    assert any("Bob took over on Grok Bot" in m["summary"] for m in webhook_calls)
    assert any("Bob is back on RD" in m["summary"] for m in webhook_calls)
