"""Unit tests for porting Fleet Librarian to an RD persona role with dormant Grok Bot fallback
(#3704, next slice of epic #3695's cutover-tracking checklist)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import staff.librarian_persona as lb
from staff.barb_continuity import BarbContinuityStore
from staff.provider_failover_config import FailoverStep
from staff.role_capsule import RoleCapsuleStore
from staff.role_health import RoleHealthStore
from staff.roles import clear_roles_cache, load_roles
from staff.validator import validate_role_data

from scripts import rd_barb_provider as rp

pytestmark = pytest.mark.unit
LA = ZoneInfo("America/Los_Angeles")


def test_librarian_yaml_passes_schema_validation_and_loads_into_roster(tmp_path: Path) -> None:
    assert lb.write_librarian_role_yaml(tmp_path).name == "librarian.yml"
    spec = lb.load_librarian_role_spec(tmp_path)
    assert spec.valid, spec.errors
    assert validate_role_data(spec_raw := lb.load_librarian_role_spec(tmp_path, raw=True)) == []
    assert spec_raw["permissions"]["lease"] is True
    assert spec_raw["permissions"]["push_branch"] is True
    assert spec_raw["permissions"]["open_pr"] is True
    assert spec_raw["permissions"]["merge"] is False
    assert spec_raw["window"] is None
    assert spec_raw["reports_to"] == "barb" and spec_raw["surface"] == "dashboard"
    assert spec_raw["providers"] == ["claude"]
    assert "retry" not in spec_raw
    clear_roles_cache()
    role = load_roles(tmp_path)["librarian"]
    assert role.valid
    assert role.scope["schedules"] == dict(lb.LIBRARIAN_SCHEDULE_ENTRIES)
    assert role.scope["naming"] == "Author - Title"
    assert role.repos == lb.LIBRARIAN_REPOS
    slots = lb.next_librarian_slots(datetime(2026, 11, 2, 7, 30, tzinfo=LA), role)
    assert slots["weekly_lesson_review"].startswith("2026-11-06T10:00:00")


def test_librarian_rd_barb_provider_chain_is_claude_only() -> None:
    # The real staff/roles/librarian.yml lists a single provider (claude) and has no retry
    # block, so the static PERSONA_ROLE_CHAINS entry is a one-step chain, matching the real
    # file's single-provider scope -- never falling back to rd_barb_provider's generic
    # multi-provider DEFAULT_CHAIN.
    assert rp.select({}, role="librarian") == rp.Decision("claude", "opus", False)
    assert rp.chain({}, role="librarian") == (FailoverStep("claude", "opus"),)


def test_librarian_capsule_schema_validation_and_persistence(tmp_path: Path) -> None:
    capsule_store = RoleCapsuleStore(tmp_path / "capsules")
    item = {
        "id": "lesson-1",
        "outcome": "accepted",
        "summary": "Promoted a fleet-wide lesson on worktree nesting into fleet-rules/",
        "link": "https://example.com/lesson/1",
    }
    payload = lb.build_librarian_capsule(
        last_run_date="2026-11-06",
        lesson_decisions=[item],
        next_slots=lb.next_librarian_slots(datetime(2026, 11, 6, 10, 5, tzinfo=LA)),
    )
    assert lb.validate_librarian_capsule(payload) == []
    saved = lb.save_librarian_capsule(capsule_store, payload, epoch=1, now=1700000000.0)
    assert saved["role"] == "librarian"
    assert (tmp_path / "capsules" / "barbara-mirror" / "capsules" / "librarian.json").is_file()
    bad_date = lb.validate_librarian_capsule({**payload, "last_scorecard_date": "bad"})
    assert any("last_scorecard_date" in e for e in bad_date)
    bad_items = lb.validate_librarian_capsule({**payload, "lesson_decisions": [123]})
    assert any("lesson_decisions" in e for e in bad_items)
    bad_outcome = {**payload, "lesson_decisions": [{**payload["lesson_decisions"][0], "outcome": "not-an-outcome"}]}
    assert any("outcome" in e for e in lb.validate_librarian_capsule(bad_outcome))
    assert any("next_slots" in e for e in lb.validate_librarian_capsule({**payload, "next_slots": {}}))


def test_shadow_run_posts_to_rd_thread_and_suppresses_webhook(tmp_path: Path) -> None:
    webhook_calls: list[Any] = []
    thread_posts: list[tuple[str, str, str]] = []
    cfg = {
        "RD_LIBRARIAN_SHADOW": "1",
        "RD_LIBRARIAN_THREAD_ID": "th_librarian_shadow",
        "RD_BARB_WEBHOOK_URL": "https://hook.example/librarian",
        "RD_BARB_WEBHOOK_KEY": "k",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "librarian-notify.json",
        "sender": lambda *a: webhook_calls.append(a),
        "thread_poster": lambda tid, body, key: thread_posts.append((tid, body, key)),
    }
    res = lb.run_librarian_slot(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        job="weekly_lesson_review",
        slot="2026-11-06T10:00",
        run_date="2026-11-06",
        notify_opts=opts,
        summary="Librarian weekly lesson review (shadow)",
        body="Accepted 1 lesson; promoted into fleet-rules/.",
        lesson_decisions=[
            {
                "id": "lesson-1",
                "outcome": "accepted",
                "summary": "Promoted a fleet-wide lesson on worktree nesting into fleet-rules/",
                "link": "https://example.com/lesson/1",
            }
        ],
        now=datetime(2026, 11, 6, 10, 0, tzinfo=LA),
    )
    assert res["status"] == "executed" and res["notify"] == {"ok": True, "status": "shadow"}
    assert webhook_calls == [] and len(thread_posts) == 1 and thread_posts[0][0] == "th_librarian_shadow"


def test_staging_fallback_drill_and_slot_skip_after_fallback_ledger_entry(tmp_path: Path) -> None:
    webhook_calls: list[dict[str, Any]] = []
    cfg = {
        "RD_BARB_WEBHOOK_URL": "https://hook.example/librarian",
        "RD_BARB_WEBHOOK_KEY": "k",
        "RD_LIBRARIAN_THREAD_ID": "th_librarian",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "librarian-drill-notify.json",
        "sender": lambda _url, _hdrs, msg: webhook_calls.append(msg),
    }
    drill = lb.run_librarian_fallback_drill(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        RoleHealthStore(tmp_path / "role-health.json"),
        slot="2026-11-06T10:00",
        run_date="2026-11-06",
        notify_opts=opts,
        base_time=datetime(2026, 11, 6, 10, 0, tzinfo=LA),
    )
    assert drill["takeover_notes"] == 1 and drill["back_notes"] == 1
    assert drill["rd_slot_after_resume"] == {
        "status": "skipped",
        "reason": "slot_already_executed",
        "slot_key": "librarian/weekly_lesson_review/2026-11-06T10:00",
    }
    assert drill["final_capsule"]["epoch"] == 2
    assert any("Librarian took over on Grok Bot" in m["summary"] for m in webhook_calls)
    assert any("Librarian is back on RD" in m["summary"] for m in webhook_calls)
