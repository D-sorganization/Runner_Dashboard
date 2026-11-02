"""Unit tests for porting Issue Remediator to an RD persona role with dormant Grok Bot fallback
(#3704, next slice of epic #3695's cutover-tracking checklist)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import staff.issue_remediator_persona as ir
from staff.barb_continuity import BarbContinuityStore
from staff.provider_failover_config import FailoverStep
from staff.role_capsule import RoleCapsuleStore
from staff.role_health import RoleHealthStore
from staff.roles import clear_roles_cache, load_roles
from staff.validator import validate_role_data

from scripts import rd_barb_provider as rp

pytestmark = pytest.mark.unit
LA = ZoneInfo("America/Los_Angeles")


def test_issue_remediator_yaml_passes_schema_validation_and_loads_into_roster(tmp_path: Path) -> None:
    assert ir.write_issue_remediator_role_yaml(tmp_path).name == "issue-remediator.yml"
    spec = ir.load_issue_remediator_role_spec(tmp_path)
    assert spec.valid, spec.errors
    assert validate_role_data(spec_raw := ir.load_issue_remediator_role_spec(tmp_path, raw=True)) == []
    assert spec_raw["permissions"]["lease"] is True
    assert spec_raw["permissions"]["push_branch"] is True
    assert spec_raw["permissions"]["open_pr"] is True
    assert spec_raw["permissions"]["merge"] is False
    assert spec_raw["window"] == {"start": "22:00", "end": "06:00"}
    assert spec_raw["reports_to"] == "night-watch" and spec_raw["surface"] == "dashboard"
    assert spec_raw["providers"] == ["claude", "codex", "antigravity"]
    assert "retry" not in spec_raw
    clear_roles_cache()
    role = load_roles(tmp_path)["issue-remediator"]
    assert role.valid
    assert role.scope["schedules"] == dict(ir.ISSUE_REMEDIATOR_SCHEDULE_ENTRIES)
    assert role.scope["targets"] == list(ir.ISSUE_REMEDIATOR_TARGETS)
    assert role.scope["skips"] == list(ir.ISSUE_REMEDIATOR_SKIPS)
    assert role.repos == ir.ISSUE_REMEDIATOR_REPOS
    slots = ir.next_issue_remediator_slots(datetime(2026, 11, 2, 7, 30, tzinfo=LA), role)
    assert slots["overnight_remediation"].startswith("2026-11-02T23:00:00")


def test_issue_remediator_rd_barb_provider_chain_has_no_cursor_agent_step() -> None:
    # The real staff/roles/issue-remediator.yml has no retry block, so provider selection for
    # rd-barb (the one adapter that decides "which CLI runs next") relies on the static
    # PERSONA_ROLE_CHAINS entry, matching the real file's 3-provider list (no cursor-agent).
    # rd_barb_provider.chain() defaults a bare claude head to "opus" when nothing overrides it.
    assert rp.select({}, role="issue-remediator") == rp.Decision("claude", "opus", False)
    expected_chain = (FailoverStep("claude", None), FailoverStep("codex", None), FailoverStep("antigravity", None))
    assert rp.chain({}, role="issue-remediator") == (FailoverStep("claude", "opus"), *expected_chain[1:])
    sent: list[dict[str, Any]] = []
    args = ["--role", "issue-remediator", "next", "--current", "claude", "--failure", "rate_limited"]
    rc = rp.main(args, config={}, notify=sent.append)
    assert rc == 0 and len(sent) == 1
    assert sent[0]["sender_role"] == "issue-remediator" and "codex" in sent[0]["summary"]


def test_issue_remediator_capsule_schema_validation_and_persistence(tmp_path: Path) -> None:
    capsule_store = RoleCapsuleStore(tmp_path / "capsules")
    item = {
        "id": "issue-1",
        "outcome": "progressed",
        "summary": "Added a failing regression and partial fix for #1234",
        "link": "https://example.com/issue/1234",
    }
    payload = ir.build_issue_remediator_capsule(
        last_run_date="2026-11-03",
        remediated_issues=[item],
        next_slots=ir.next_issue_remediator_slots(datetime(2026, 11, 3, 23, 5, tzinfo=LA)),
    )
    assert ir.validate_issue_remediator_capsule(payload) == []
    saved = ir.save_issue_remediator_capsule(capsule_store, payload, epoch=1, now=1700000000.0)
    assert saved["role"] == "issue-remediator"
    assert (tmp_path / "capsules" / "barbara-mirror" / "capsules" / "issue-remediator.json").is_file()
    bad_date = ir.validate_issue_remediator_capsule({**payload, "last_scorecard_date": "bad"})
    assert any("last_scorecard_date" in e for e in bad_date)
    bad_items = ir.validate_issue_remediator_capsule({**payload, "remediated_issues": [123]})
    assert any("remediated_issues" in e for e in bad_items)
    bad_outcome = {**payload, "remediated_issues": [{**payload["remediated_issues"][0], "outcome": "not-an-outcome"}]}
    assert any("outcome" in e for e in ir.validate_issue_remediator_capsule(bad_outcome))
    assert any("next_slots" in e for e in ir.validate_issue_remediator_capsule({**payload, "next_slots": {}}))


def test_shadow_run_posts_to_rd_thread_and_suppresses_webhook(tmp_path: Path) -> None:
    webhook_calls: list[Any] = []
    thread_posts: list[tuple[str, str, str]] = []
    cfg = {
        "RD_ISSUE_REMEDIATOR_SHADOW": "1",
        "RD_ISSUE_REMEDIATOR_THREAD_ID": "th_issue_remediator_shadow",
        "RD_BARB_WEBHOOK_URL": "https://hook.example/issue-remediator",
        "RD_BARB_WEBHOOK_KEY": "k",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "issue-remediator-notify.json",
        "sender": lambda *a: webhook_calls.append(a),
        "thread_poster": lambda tid, body, key: thread_posts.append((tid, body, key)),
    }
    res = ir.run_issue_remediator_slot(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        job="overnight_remediation",
        slot="2026-11-03T23:00",
        run_date="2026-11-03",
        notify_opts=opts,
        summary="Issue Remediator overnight pass (shadow)",
        body="Progressed 1 issue; no closures.",
        remediated_issues=[
            {
                "id": "issue-1",
                "outcome": "progressed",
                "summary": "Added a failing regression and partial fix for #1234",
                "link": "https://example.com/issue/1234",
            }
        ],
        now=datetime(2026, 11, 3, 23, 0, tzinfo=LA),
    )
    assert res["status"] == "executed" and res["notify"] == {"ok": True, "status": "shadow"}
    assert webhook_calls == [] and len(thread_posts) == 1 and thread_posts[0][0] == "th_issue_remediator_shadow"


def test_staging_fallback_drill_and_slot_skip_after_fallback_ledger_entry(tmp_path: Path) -> None:
    webhook_calls: list[dict[str, Any]] = []
    cfg = {
        "RD_BARB_WEBHOOK_URL": "https://hook.example/issue-remediator",
        "RD_BARB_WEBHOOK_KEY": "k",
        "RD_ISSUE_REMEDIATOR_THREAD_ID": "th_issue_remediator",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "issue-remediator-drill-notify.json",
        "sender": lambda _url, _hdrs, msg: webhook_calls.append(msg),
    }
    drill = ir.run_issue_remediator_fallback_drill(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        RoleHealthStore(tmp_path / "role-health.json"),
        slot="2026-11-03T23:00",
        run_date="2026-11-03",
        notify_opts=opts,
        base_time=datetime(2026, 11, 3, 23, 50, tzinfo=LA),
    )
    assert drill["takeover_notes"] == 1 and drill["back_notes"] == 1
    assert drill["rd_slot_after_resume"] == {
        "status": "skipped",
        "reason": "slot_already_executed",
        "slot_key": "issue-remediator/overnight_remediation/2026-11-03T23:00",
    }
    assert drill["final_capsule"]["epoch"] == 2
    assert any("Issue Remediator took over on Grok Bot" in m["summary"] for m in webhook_calls)
    assert any("Issue Remediator is back on RD" in m["summary"] for m in webhook_calls)
