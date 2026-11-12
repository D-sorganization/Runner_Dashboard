"""Unit tests for porting Pragmatic Programmer to an RD persona role with dormant Grok Bot
fallback (#3704, next slice of epic #3695's cutover-tracking checklist)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import staff.pragmatic_programmer_persona as pp
from staff.barb_continuity import BarbContinuityStore
from staff.provider_failover_config import FailoverStep, resolve_failover_chain
from staff.role_capsule import RoleCapsuleStore
from staff.role_health import RoleHealthStore
from staff.roles import clear_roles_cache, load_roles
from staff.validator import validate_role_data

from scripts import rd_barb_provider as rp

pytestmark = pytest.mark.unit
LA = ZoneInfo("America/Los_Angeles")


def test_pragmatic_programmer_yaml_passes_schema_validation_and_loads_into_roster(tmp_path: Path) -> None:
    assert pp.write_pragmatic_programmer_role_yaml(tmp_path).name == "pragmatic-programmer.yml"
    spec = pp.load_pragmatic_programmer_role_spec(tmp_path)
    assert spec.valid, spec.errors
    assert validate_role_data(spec_raw := pp.load_pragmatic_programmer_role_spec(tmp_path, raw=True)) == []
    assert spec_raw["permissions"]["code_read_only"] is True
    assert not any(spec_raw["permissions"][k] for k in ("open_pr", "merge", "lease", "push_branch", "notify_user"))
    assert spec_raw["reports_to"] == "barb" and spec_raw["surface"] == "both"
    assert spec_raw["providers"] == ["claude", "codex", "antigravity", "cursor-agent"]
    clear_roles_cache()
    role = load_roles(tmp_path)["pragmatic-programmer"]
    assert role.valid and role.code_read_only is True
    assert role.scope["schedules"] == dict(pp.PRAGMATIC_PROGRAMMER_SCHEDULE_ENTRIES)
    slots = pp.next_pragmatic_programmer_slots(datetime(2026, 11, 2, 7, 30, tzinfo=LA), role)
    assert slots["weekly_review"].startswith("2026-11-06T13:00:00")


def test_pragmatic_programmer_resolved_chain_has_no_model_override(tmp_path: Path) -> None:
    pp.write_pragmatic_programmer_role_yaml(tmp_path)
    resolved = resolve_failover_chain(pp.load_pragmatic_programmer_role_spec(tmp_path))
    assert resolved == pp.PRAGMATIC_PROGRAMMER_FAILOVER_CHAIN
    assert resolved[0] == FailoverStep("claude", None)
    assert [s.provider for s in resolved] == ["claude", "codex", "antigravity", "cursor-agent"]
    # rd_barb_provider.chain() defaults a bare claude head to "opus" when nothing overrides it
    # (shared DEFAULT_PROVIDER/DEFAULT_MODEL rule, not something this role's own chain sets).
    assert rp.select({}, role="pragmatic-programmer") == rp.Decision("claude", "opus", False)
    assert rp.chain({}, role="pragmatic-programmer") == (
        FailoverStep("claude", "opus"),
        *pp.PRAGMATIC_PROGRAMMER_FAILOVER_CHAIN[1:],
    )
    sent: list[dict[str, Any]] = []
    rc = rp.main(
        ["--role", "pragmatic-programmer", "next", "--current", "claude", "--failure", "rate_limited"],
        config={},
        notify=sent.append,
    )
    assert rc == 0 and len(sent) == 1
    assert sent[0]["sender_role"] == "pragmatic-programmer" and "codex" in sent[0]["summary"]


def test_pragmatic_programmer_capsule_schema_validation_and_persistence(tmp_path: Path) -> None:
    capsule_store = RoleCapsuleStore(tmp_path / "capsules")
    findings = [
        {"id": "finding-5550", "summary": "DRY violation in router dispatch", "link": "https://example.com/5550"}
    ]
    payload = pp.build_pragmatic_programmer_capsule(
        last_review_date="2026-11-06",
        findings=findings,
        next_slots=pp.next_pragmatic_programmer_slots(datetime(2026, 11, 6, 13, 5, tzinfo=LA)),
    )
    assert pp.validate_pragmatic_programmer_capsule(payload) == []
    saved = pp.save_pragmatic_programmer_capsule(capsule_store, payload, epoch=1, now=1700000000.0)
    assert saved["role"] == "pragmatic-programmer"
    assert (tmp_path / "capsules" / "barbara-mirror" / "capsules" / "pragmatic-programmer.json").is_file()
    bad_date = {**payload, "last_scorecard_date": "bad"}
    assert any("last_scorecard_date" in e for e in pp.validate_pragmatic_programmer_capsule(bad_date))
    bad_findings = {**payload, "findings": [{}]}
    assert any("findings" in e for e in pp.validate_pragmatic_programmer_capsule(bad_findings))
    bad_slots = {**payload, "next_slots": {}}
    assert any("next_slots" in e for e in pp.validate_pragmatic_programmer_capsule(bad_slots))


def test_shadow_run_posts_to_rd_thread_and_suppresses_webhook(tmp_path: Path) -> None:
    webhook_calls: list[Any] = []
    thread_posts: list[tuple[str, str, str]] = []
    cfg = {
        "RD_PRAGMATIC_PROGRAMMER_SHADOW": "1",
        "RD_PRAGMATIC_PROGRAMMER_THREAD_ID": "th_pragmatic_programmer_shadow",
        "RD_BARB_WEBHOOK_URL": "https://hook.example/pragmatic-programmer",
        "RD_BARB_WEBHOOK_KEY": "k",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "pragmatic-programmer-notify.json",
        "sender": lambda *a: webhook_calls.append(a),
        "thread_poster": lambda tid, body, key: thread_posts.append((tid, body, key)),
    }
    res = pp.run_pragmatic_programmer_slot(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        job="weekly_review",
        slot="2026-11-06T13:00",
        review_date="2026-11-06",
        notify_opts=opts,
        summary="Pragmatic Programmer weekly review (shadow)",
        body="1 DRY finding in router dispatch; no blocking issues.",
        findings=[
            {"id": "finding-1", "summary": "DRY violation in router dispatch", "link": "https://example.com/3704"}
        ],
        now=datetime(2026, 11, 6, 13, 0, tzinfo=LA),
    )
    assert res["status"] == "executed" and res["notify"] == {"ok": True, "status": "shadow"}
    assert webhook_calls == [] and len(thread_posts) == 1 and thread_posts[0][0] == "th_pragmatic_programmer_shadow"


def test_staging_fallback_drill_and_slot_skip_after_fallback_ledger_entry(tmp_path: Path) -> None:
    webhook_calls: list[dict[str, Any]] = []
    cfg = {
        "RD_BARB_WEBHOOK_URL": "https://hook.example/pragmatic-programmer",
        "RD_BARB_WEBHOOK_KEY": "k",
        "RD_PRAGMATIC_PROGRAMMER_THREAD_ID": "th_pragmatic_programmer",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "pragmatic-programmer-drill-notify.json",
        "sender": lambda _url, _hdrs, msg: webhook_calls.append(msg),
    }
    drill = pp.run_pragmatic_programmer_fallback_drill(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        RoleHealthStore(tmp_path / "role-health.json"),
        slot="2026-11-06T13:00",
        review_date="2026-11-06",
        notify_opts=opts,
        base_time=datetime(2026, 11, 6, 12, 50, tzinfo=LA),
    )
    assert drill["takeover_notes"] == 1 and drill["back_notes"] == 1
    assert drill["rd_slot_after_resume"] == {
        "status": "skipped",
        "reason": "slot_already_executed",
        "slot_key": "pragmatic-programmer/weekly_review/2026-11-06T13:00",
    }
    assert drill["final_capsule"]["epoch"] == 2
    assert any("Pragmatic Programmer took over on Grok Bot" in m["summary"] for m in webhook_calls)
    assert any("Pragmatic Programmer is back on RD" in m["summary"] for m in webhook_calls)
