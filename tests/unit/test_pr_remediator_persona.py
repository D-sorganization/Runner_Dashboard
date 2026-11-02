"""Unit tests for porting PR Remediator to an RD persona role with dormant Grok Bot fallback (#3704)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import staff.pr_remediator_persona as pr_
from staff.barb_continuity import BarbContinuityStore
from staff.provider_failover_config import FailoverStep
from staff.provider_fallback import match_provider_fallback, validate_provider_fallback_rules_data
from staff.role_capsule import RoleCapsuleStore
from staff.role_health import RoleHealthStore
from staff.roles import clear_roles_cache, load_roles
from staff.validator import validate_role_data

from scripts import rd_barb_provider as rp

pytestmark = pytest.mark.unit
LA = ZoneInfo("America/Los_Angeles")


def test_pr_remediator_yaml_passes_schema_validation_and_loads_into_roster(tmp_path: Path) -> None:
    assert pr_.write_pr_remediator_role_yaml(tmp_path).name == "pr-remediator.yml"
    spec = pr_.load_pr_remediator_role_spec(tmp_path)
    assert spec.valid, spec.errors
    assert validate_role_data(spec_raw := pr_.load_pr_remediator_role_spec(tmp_path, raw=True)) == []
    perms = spec_raw["permissions"]
    assert all(perms[k] for k in ("lease", "push_branch", "open_pr"))
    assert not any(perms[k] for k in ("merge", "host_shell", "notify_user"))
    assert spec_raw["reports_to"] == "night-watch" and spec_raw["surface"] == "dashboard"
    assert spec_raw["providers"] == ["claude", "codex", "antigravity"]
    scope = spec_raw["scope"]
    assert scope["targets"] == list(pr_.PR_REMEDIATOR_TARGETS) and scope["skips"] == list(pr_.PR_REMEDIATOR_SKIPS)
    assert scope["max_lease_hours"] == 2
    assert spec_raw["strategy"]["consolidate_when"] == {"open_prs": 6, "utilisation_pct": 70}
    clear_roles_cache()
    role = load_roles(tmp_path)["pr-remediator"]
    assert role.valid
    assert role.scope["schedules"] == dict(pr_.PR_REMEDIATOR_SCHEDULE_ENTRIES)
    slots = pr_.next_pr_remediator_slots(datetime(2026, 11, 2, 0, 30, tzinfo=LA), role)
    assert slots["nightly_remediation"].startswith("2026-11-02T01:00:00")


def test_pr_remediator_declarative_fallback_matches_real_role_yaml(tmp_path: Path) -> None:
    pr_.write_pr_remediator_role_yaml(tmp_path)
    assert validate_provider_fallback_rules_data(pr_.load_pr_remediator_role_spec(tmp_path, raw=True)) == []
    spec = pr_.load_pr_remediator_role_spec(tmp_path)
    assert spec.failover_chain == () and len(spec.provider_fallbacks) == 1
    rule = spec.provider_fallbacks[0]
    assert rule.from_provider == "codex" and rule.target.provider == "antigravity"
    assert rule.target.model == "gemini-3.8-flash-high"
    fb = spec.provider_fallbacks
    assert match_provider_fallback(fb, "provider_capacity", "codex", retries_exhausted=False) is rule
    assert match_provider_fallback(fb, "rate_limited", "codex", retries_exhausted=False) is None
    assert match_provider_fallback(fb, "rate_limited", "codex", retries_exhausted=True) is rule


def test_pr_remediator_rd_barb_provider_chain_has_no_cursor_agent_step() -> None:
    assert rp.select({}, role="pr-remediator") == rp.Decision("claude", "opus", False)
    chain = (FailoverStep("claude", "opus"), FailoverStep("codex", None), FailoverStep("antigravity", None))
    assert rp.chain({}, role="pr-remediator") == chain
    sent: list[dict[str, Any]] = []
    args = ["--role", "pr-remediator", "next", "--current", "claude", "--failure", "rate_limited"]
    rc = rp.main(args, config={}, notify=sent.append)
    assert rc == 0 and len(sent) == 1
    assert sent[0]["sender_role"] == "pr-remediator" and "codex" in sent[0]["summary"]


def test_pr_remediator_capsule_schema_validation_and_persistence(tmp_path: Path) -> None:
    capsule_store = RoleCapsuleStore(tmp_path / "capsules")
    remediated = [
        {"id": "pr-3750", "outcome": "rebased", "summary": "Rebased onto fresh main", "link": "https://x/3750"}
    ]
    payload = pr_.build_pr_remediator_capsule(
        last_run_date="2026-11-02",
        remediated_prs=remediated,
        next_slots=pr_.next_pr_remediator_slots(datetime(2026, 11, 2, 1, 5, tzinfo=LA)),
    )
    assert pr_.validate_pr_remediator_capsule(payload) == []
    saved = pr_.save_pr_remediator_capsule(capsule_store, payload, epoch=1, now=1700000000.0)
    assert saved["role"] == "pr-remediator"
    assert (tmp_path / "capsules" / "barbara-mirror" / "capsules" / "pr-remediator.json").is_file()
    bad_cases = [
        ({"last_scorecard_date": "bad"}, "last_scorecard_date"),
        ({"remediated_prs": [{}]}, "remediated_prs"),
        ({"remediated_prs": [{**remediated[0], "outcome": "exploded"}]}, "outcome"),
        ({"next_slots": {}}, "next_slots"),
    ]
    for override, needle in bad_cases:
        assert any(needle in e for e in pr_.validate_pr_remediator_capsule({**payload, **override}))


def test_shadow_run_posts_to_rd_thread_and_suppresses_webhook(tmp_path: Path) -> None:
    webhook_calls: list[Any] = []
    thread_posts: list[tuple[str, str, str]] = []
    cfg = {
        "RD_PR_REMEDIATOR_SHADOW": "1",
        "RD_PR_REMEDIATOR_THREAD_ID": "th_pr_remediator_shadow",
        "RD_BARB_WEBHOOK_URL": "https://hook.example/pr-remediator",
        "RD_BARB_WEBHOOK_KEY": "k",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "pr-remediator-notify.json",
        "sender": lambda *a: webhook_calls.append(a),
        "thread_poster": lambda tid, body, key: thread_posts.append((tid, body, key)),
    }
    remediated_prs = [{"id": "pr-1", "outcome": "merged", "summary": "Rebased onto fresh main", "link": "https://x/1"}]
    res = pr_.run_pr_remediator_slot(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        job="nightly_remediation",
        slot="2026-11-02T01:00",
        run_date="2026-11-02",
        notify_opts=opts,
        summary="PR Remediator nightly pass (shadow)",
        body="3 PRs remediated; 0 escalated.",
        remediated_prs=remediated_prs,
        now=datetime(2026, 11, 2, 1, 0, tzinfo=LA),
    )
    assert res["status"] == "executed" and res["notify"] == {"ok": True, "status": "shadow"}
    assert webhook_calls == [] and len(thread_posts) == 1 and thread_posts[0][0] == "th_pr_remediator_shadow"


def test_staging_fallback_drill_and_slot_skip_after_fallback_ledger_entry(tmp_path: Path) -> None:
    webhook_calls: list[dict[str, Any]] = []
    cfg = {
        "RD_BARB_WEBHOOK_URL": "https://hook.example/pr-remediator",
        "RD_BARB_WEBHOOK_KEY": "k",
        "RD_PR_REMEDIATOR_THREAD_ID": "th_pr_remediator",
    }
    opts = {
        "config": cfg,
        "state_path": tmp_path / "pr-remediator-drill-notify.json",
        "sender": lambda _url, _hdrs, msg: webhook_calls.append(msg),
    }
    drill = pr_.run_pr_remediator_fallback_drill(
        BarbContinuityStore(tmp_path / "continuity.db"),
        RoleCapsuleStore(tmp_path / "capsules"),
        RoleHealthStore(tmp_path / "role-health.json"),
        slot="2026-11-02T01:00",
        run_date="2026-11-02",
        notify_opts=opts,
        base_time=datetime(2026, 11, 3, 0, 50, tzinfo=LA),
    )
    assert drill["takeover_notes"] == 1 and drill["back_notes"] == 1
    assert drill["rd_slot_after_resume"] == {
        "status": "skipped",
        "reason": "slot_already_executed",
        "slot_key": "pr-remediator/nightly_remediation/2026-11-02T01:00",
    }
    assert drill["final_capsule"]["epoch"] == 2
    assert any("PR Remediator took over on Grok Bot" in m["summary"] for m in webhook_calls)
    assert any("PR Remediator is back on RD" in m["summary"] for m in webhook_calls)
