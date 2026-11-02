"""Unit tests for porting Vision Quest to an RD persona role with dormant Grok Bot fallback
(#3704, next slice of epic #3695's cutover-tracking checklist)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import staff.vision_quest_persona as vq
from staff.barb_continuity import BarbContinuityStore
from staff.provider_failover_config import FailoverStep
from staff.role_capsule import RoleCapsuleStore
from staff.role_health import RoleHealthStore
from staff.roles import clear_roles_cache, load_roles
from staff.validator import validate_role_data

from scripts import rd_barb_provider as rp

pytestmark = pytest.mark.unit
LA = ZoneInfo("America/Los_Angeles")


def test_vision_quest_yaml_passes_schema_validation_and_loads_into_roster(tmp_path: Path) -> None:
    assert vq.write_vision_quest_role_yaml(tmp_path).name == "vision-quest.yml"
    spec = vq.load_vision_quest_role_spec(tmp_path)
    assert spec.valid, spec.errors
    assert validate_role_data(spec_raw := vq.load_vision_quest_role_spec(tmp_path, raw=True)) == []
    assert spec_raw["permissions"]["lease"] is False
    assert spec_raw["permissions"]["push_branch"] is True
    assert spec_raw["permissions"]["open_pr"] is True
    assert spec_raw["permissions"]["merge"] is False
    assert spec_raw["reports_to"] == "user" and spec_raw["surface"] == "dashboard"
    assert spec_raw["providers"] == ["claude", "antigravity", "codex", "cursor-agent"]
    assert "retry" not in spec_raw
    clear_roles_cache()
    role = load_roles(tmp_path)["vision-quest"]
    assert role.valid
    assert role.scope["schedules"] == dict(vq.VISION_QUEST_SCHEDULE_ENTRIES)
    assert role.scope["quorum"] == 2
    assert role.scope["does_not"] == ["decide", "file-issues", "submit-proposals", "implement", "lease"]
    assert role.repos == vq.VISION_QUEST_REPOS
    slots = vq.next_vision_quest_slots(datetime(2026, 11, 2, 7, 30, tzinfo=LA), role)
    assert slots["monthly_quest"].startswith("2026-12-01T09:00:00")


def test_vision_quest_rd_barb_provider_chain_has_no_retry_block_in_real_yaml() -> None:
    # No retry block in the real yaml -> rd-barb provider selection uses the static
    # PERSONA_ROLE_CHAINS entry, matching the real file's 4-provider list.
    assert rp.select({}, role="vision-quest") == rp.Decision("claude", "opus", False)
    rest = (FailoverStep("antigravity", None), FailoverStep("codex", None), FailoverStep("cursor-agent", None))
    assert rp.chain({}, role="vision-quest") == (FailoverStep("claude", "opus"), *rest)
    sent: list[dict[str, Any]] = []
    args = ["--role", "vision-quest", "next", "--current", "claude", "--failure", "rate_limited"]
    rc = rp.main(args, config={}, notify=sent.append)
    assert rc == 0 and len(sent) == 1
    assert sent[0]["sender_role"] == "vision-quest" and "antigravity" in sent[0]["summary"]


def test_vision_quest_capsule_schema_validation_and_persistence(tmp_path: Path) -> None:
    capsule_store = RoleCapsuleStore(tmp_path / "capsules")
    avenue = {
        "lens": "theorist",
        "title": "Reframe the cooling-rate model as a two-regime fit",
        "grounding": "inferred",
        "link": "https://example.com/vision-quest/2026-11-01-cooling",
    }
    payload = vq.build_vision_quest_capsule(
        last_quest_date="2026-11-01",
        avenues=[avenue],
        next_slots=vq.next_vision_quest_slots(datetime(2026, 11, 1, 9, 5, tzinfo=LA)),
    )
    assert vq.validate_vision_quest_capsule(payload) == []
    saved = vq.save_vision_quest_capsule(capsule_store, payload, epoch=1, now=1700000000.0)
    assert saved["role"] == "vision-quest"
    assert (tmp_path / "capsules" / "barbara-mirror" / "capsules" / "vision-quest.json").is_file()
    bad_date = vq.validate_vision_quest_capsule({**payload, "last_scorecard_date": "bad"})
    assert any("last_scorecard_date" in e for e in bad_date)
    bad_avenues = vq.validate_vision_quest_capsule({**payload, "avenues": [123]})
    assert any("avenues" in e for e in bad_avenues)
    bad_lens = {**payload, "avenues": [{**avenue, "lens": "not-a-lens"}]}
    assert any("lens" in e for e in vq.validate_vision_quest_capsule(bad_lens))
    bad_grounding = {**payload, "avenues": [{**avenue, "grounding": "not-a-grounding"}]}
    assert any("grounding" in e for e in vq.validate_vision_quest_capsule(bad_grounding))
    assert any("next_slots" in e for e in vq.validate_vision_quest_capsule({**payload, "next_slots": {}}))


def test_shadow_run_posts_to_rd_thread_and_suppresses_webhook(tmp_path: Path) -> None:
    webhook_calls: list[Any] = []
    thread_posts: list[tuple[str, str, str]] = []
    cfg = {
        "RD_VISION_QUEST_SHADOW": "1",
        "RD_VISION_QUEST_THREAD_ID": "th_vision_quest_shadow",
        "RD_BARB_WEBHOOK_URL": "https://hook.example/vision-quest",
        "RD_BARB_WEBHOOK_KEY": "k",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "vision-quest-notify.json",
        "sender": lambda *a: webhook_calls.append(a),
        "thread_poster": lambda tid, body, key: thread_posts.append((tid, body, key)),
    }
    res = vq.run_vision_quest_slot(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        job="monthly_quest",
        slot="2026-11-01T09:00",
        quest_date="2026-11-01",
        notify_opts=opts,
        summary="Vision Quest monthly brief (shadow)",
        body="3 avenues ranked; quorum met.",
        avenues=[
            {
                "lens": "experimentalist",
                "title": "Run the cheaper first experiment before the full campaign",
                "grounding": "established",
                "link": "https://example.com/vision-quest/2026-11-01-experiment",
            }
        ],
        now=datetime(2026, 11, 1, 9, 0, tzinfo=LA),
    )
    assert res["status"] == "executed" and res["notify"] == {"ok": True, "status": "shadow"}
    assert webhook_calls == [] and len(thread_posts) == 1 and thread_posts[0][0] == "th_vision_quest_shadow"


def test_staging_fallback_drill_and_slot_skip_after_fallback_ledger_entry(tmp_path: Path) -> None:
    webhook_calls: list[dict[str, Any]] = []
    cfg = {
        "RD_BARB_WEBHOOK_URL": "https://hook.example/vision-quest",
        "RD_BARB_WEBHOOK_KEY": "k",
        "RD_VISION_QUEST_THREAD_ID": "th_vision_quest",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "vision-quest-drill-notify.json",
        "sender": lambda _url, _hdrs, msg: webhook_calls.append(msg),
    }
    drill = vq.run_vision_quest_fallback_drill(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        RoleHealthStore(tmp_path / "role-health.json"),
        slot="2026-12-01T09:00",
        quest_date="2026-12-01",
        notify_opts=opts,
        base_time=datetime(2026, 12, 1, 9, 50, tzinfo=LA),
    )
    assert drill["takeover_notes"] == 1 and drill["back_notes"] == 1
    assert drill["rd_slot_after_resume"] == {
        "status": "skipped",
        "reason": "slot_already_executed",
        "slot_key": "vision-quest/monthly_quest/2026-12-01T09:00",
    }
    assert drill["final_capsule"]["epoch"] == 2
    assert any("Vision Quest took over on Grok Bot" in m["summary"] for m in webhook_calls)
    assert any("Vision Quest is back on RD" in m["summary"] for m in webhook_calls)
