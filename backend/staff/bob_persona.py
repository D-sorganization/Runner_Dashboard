"""Bob RD persona role runtime, capsule schema, shadow-mode execution, and Grok Bot fallback drill (#3296)."""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import staff.persona_fallback_lease as _fl  # noqa: E402
from staff.barb_continuity import BarbContinuityStore  # noqa: E402
from staff.provider_failover_config import FailoverStep  # noqa: E402
from staff.role_capsule import RoleCapsuleStore  # noqa: E402
from staff.role_health import RoleHealthStore  # noqa: E402
from staff.role_spec import RoleSpec, read_and_parse_role_file  # noqa: E402
from staff.schedule import DEFAULT_TZ, next_fire, parse_cron  # noqa: E402

from scripts import rd_barb_notify as _notify  # noqa: E402

LA = ZoneInfo(DEFAULT_TZ)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

BOB_SCHEDULE_ENTRIES: Mapping[str, str] = {
    "wave_scoring": "0 10,16 * * 1-5",
    "daily_note": "0 17 * * 1-5",
    "weekly_value_watch": "0 8 * * 1",
}

BOB_FAILOVER_CHAIN: tuple[FailoverStep, ...] = (
    FailoverStep("claude", "claude-opus-5-5"),
    FailoverStep("antigravity", "gemini-3.8-flash-high"),
    FailoverStep("cursor-agent", None),
    FailoverStep("codex", "gpt-6.1-sol"),
)


def bob_role_data() -> dict[str, Any]:
    """Canonical staff/roles/bob.yml mapping conforming to staff/schema.json (#3296)."""
    perms = dict.fromkeys(("lease", "push_branch", "open_pr", "merge", "host_shell"), False)
    perms.update({"notify_user": True, "code_read_only": True})
    chain = [{"provider": s.provider, **({"model": s.model} if s.model else {})} for s in BOB_FAILOVER_CHAIN]
    return {
        "name": "bob",
        "title": "Bob — Productivity Watchdog",
        "summary": "Report-only productivity watchdog: wave scoring, 5 PM daily note, and Monday value watch.",
        "playbook": "docs/operations/persona-cutover.md",
        "prompt_template": None,
        "instructions": (
            "You are Bob, the fleet productivity watchdog. Report-only, never dispatches or opens PRs, "
            "and may criticize Barb. Schedules: 0 10,16 * * 1-5 (wave scoring), 0 17 * * 1-5 (daily note), "
            "0 8 * * 1 (weekly value watch) in America/Los_Angeles. Message Dieter via persona_notify."
        ),
        "providers": ["claude", "antigravity", "cursor-agent", "codex"],
        "model": "claude-opus-5-5",
        "schedule": "0 10,16,17 * * 1-5",
        "window": None,
        "repos": ["Runner_Dashboard", "Repository_Management", "UpstreamDrift", "AffineDrift", "Gasification_Model"],
        "scope": {"schedules": dict(BOB_SCHEDULE_ENTRIES), "timezone": DEFAULT_TZ, "report_only": True},
        "budget": {"usd_per_run": 0.0, "usd_per_day": 0.0},
        "permissions": perms,
        "retry": {"max_attempts": 4, "failover_chain": chain},
        "reports_to": "barb",
        "holds": [],
        "surface": "both",
        "persona": {
            "voice": "Candid productivity watchdog. Report-only, never dispatches, and may criticize Barb.",
            "greeting": "Bob here. Ask for wave scores, the 5 PM daily note, or the Monday 8 AM value watch.",
            "answers": ["10 AM and 4 PM wave scoring", "5 PM daily productivity note", "Monday 8 AM value watch"],
            "defers_to": ["barb"],
        },
        "chat": {
            "contract": "staff/prompts/_chat_contract.md",
            "providers": ["claude", "antigravity", "codex"],
            "tools": ["read_briefing", "read_priorities", "read_sessions", "read_staff_summary", "read_run"],
        },
    }


def write_bob_role_yaml(directory: Path) -> Path:
    """Write ``bob.yml`` into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "bob.yml"
    path.write_text(yaml.safe_dump(bob_role_data(), sort_keys=False), encoding="utf-8")
    return path


def load_bob_role_spec(directory: Path, *, raw: bool = False) -> Any:
    """Parse ``bob.yml`` from ``directory`` into a ``RoleSpec`` (or raw dict when ``raw=True``)."""
    path = directory / "bob.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if raw else read_and_parse_role_file(path)


def next_bob_slots(after: datetime, spec: RoleSpec | None = None) -> dict[str, str]:
    """Compute the next fire timestamp in America/Los_Angeles for each Bob schedule entry."""
    schedules = (
        dict(spec.scope.get("schedules") or BOB_SCHEDULE_ENTRIES)
        if spec is not None and isinstance(spec.scope, dict)
        else dict(BOB_SCHEDULE_ENTRIES)
    )
    return {job: next_fire(parse_cron(expr), after, tz=DEFAULT_TZ).isoformat() for job, expr in schedules.items()}


def build_bob_capsule(
    *,
    last_scorecard_date: str,
    open_recommendations: Sequence[Mapping[str, Any]],
    next_slots: Mapping[str, str],
    cursors: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Bob capsule payload with scorecard date, linked recommendations, and next slots."""
    recs = [
        {
            "id": str(r.get("id") or f"rec-{i + 1}").strip(),
            "summary": str(r.get("summary") or "").strip(),
            "link": str(r.get("link") or "").strip(),
        }
        for i, r in enumerate(open_recommendations)
    ]
    date_s = str(last_scorecard_date).strip()
    return {
        "last_scorecard_date": date_s,
        "open_recommendations": recs,
        "open_items": [{"id": r["id"], "title": r["summary"], "url": r["link"]} for r in recs],
        "cursors": dict(cursors or {"last_scorecard_date": date_s}),
        "next_slots": dict(next_slots),
    }


def validate_bob_capsule(payload: Mapping[str, Any]) -> list[str]:
    """Return contract problems for a Bob capsule payload (empty list when valid)."""
    problems: list[str] = []
    if not _DATE_RE.match(str(payload.get("last_scorecard_date") or "").strip()):
        problems.append("last_scorecard_date must be YYYY-MM-DD")
    recs = payload.get("open_recommendations")
    if not isinstance(recs, list):
        problems.append("open_recommendations must be a list")
    else:
        for i, item in enumerate(recs):
            if not isinstance(item, Mapping) or not str(item.get("summary") or "").strip():
                problems.append(f"open_recommendations[{i}].summary must be non-empty")
                continue
            if not str(item.get("link") or "").strip().startswith(("https://", "http://")):
                problems.append(f"open_recommendations[{i}].link must be a URL")
    slots = payload.get("next_slots")
    if not isinstance(slots, Mapping) or not set(BOB_SCHEDULE_ENTRIES).issubset(set(slots)):
        problems.append(f"next_slots must include {sorted(BOB_SCHEDULE_ENTRIES)}")
    return problems


def save_bob_capsule(
    store: RoleCapsuleStore, payload: Mapping[str, Any], *, epoch: int = 1, now: float | None = None
) -> dict[str, Any]:
    """Validate Bob's capsule schema and persist + mirror via RoleCapsuleStore."""
    if problems := validate_bob_capsule(payload):
        raise ValueError(f"Invalid Bob capsule: {'; '.join(problems)}")
    slots_map = dict(payload["next_slots"])
    date_s = str(payload["last_scorecard_date"])
    return store.save_capsule(
        "bob",
        epoch=epoch,
        last_scorecard_date=date_s,
        open_items=list(payload.get("open_items") or []),
        cursors={**dict(payload.get("cursors") or {}), "last_scorecard_date": date_s},
        next_slots=[f"{job}:{ts}" for job, ts in sorted(slots_map.items())],
        now=now,
    )


def run_bob_slot(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    *,
    job: str,
    slot: str,
    scorecard_date: str,
    notify_opts: Mapping[str, Any],
    summary: str = "Bob daily note",
    body: str = "",
    recommendations: Sequence[Mapping[str, Any]] = (),
    holder: str = "rd:desk",
    epoch: int = 1,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute one scheduled Bob slot unless fallback lease is active or the slot already ran."""
    clock = now or datetime.now(LA)
    key = _fl.slot_ledger_key("bob", job, slot)
    if _fl.is_scheduler_blocked_by_fallback(continuity_store, "bob", now=clock):
        return {"status": "skipped", "reason": "fallback_lease_held", "slot_key": key}
    if _fl.should_skip_slot(continuity_store, role="bob", job=job, slot=slot):
        return {"status": "skipped", "reason": "slot_already_executed", "slot_key": key}
    _fl.record_slot_claim(continuity_store, role="bob", job=job, slot=slot, holder=holder, epoch=epoch, now=clock)
    payload = build_bob_capsule(
        last_scorecard_date=scorecard_date, open_recommendations=recommendations, next_slots=next_bob_slots(clock)
    )
    saved = save_bob_capsule(capsule_store, payload, epoch=epoch, now=clock.timestamp())
    msg = {"kind": "update", "urgency": "normal", "summary": summary, "body": body, "dedupe_key": f"bob-{job}-{slot}"}
    res = _notify.persona_notify("bob", msg, **notify_opts, clock=clock.timestamp)
    return {"status": "executed", "slot_key": key, "capsule": saved, "notify": res}


def run_bob_fallback_drill(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    health_store: RoleHealthStore,
    *,
    slot: str,
    scorecard_date: str,
    notify_opts: Mapping[str, Any],
    base_time: datetime | None = None,
) -> dict[str, Any]:
    """Run the staging fallback drill for Bob: hub down -> Grok takeover -> 5 PM slot -> resume -> RD skip."""
    t0 = (base_time or datetime(2026, 10, 28, 16, 40, tzinfo=LA)).timestamp()
    dt = lambda offset: datetime.fromtimestamp(t0 + offset, tz=LA)  # noqa: E731

    def alert(key: str, summary: str, offset: float) -> dict[str, Any]:
        env = {"kind": "alert", "urgency": "high", "summary": summary, "body": summary, "dedupe_key": key}
        return _notify.persona_notify(
            "bob", {**env, "recommended_action": "Monitor RD hub."}, **notify_opts, clock=lambda: t0 + offset
        )

    health_store.record_heartbeat("bob", epoch=1, now=t0)
    health_store.record_hub_probe("bob", ok=False, now=t0 + 600.0)
    unhealthy = health_store.record_hub_probe("bob", ok=False, now=t0 + 1200.0)
    init_cap = build_bob_capsule(
        last_scorecard_date=scorecard_date, open_recommendations=[], next_slots=next_bob_slots(dt(0))
    )
    save_bob_capsule(capsule_store, init_cap, epoch=1, now=t0)
    lease_res = _fl.acquire_persona_lease(continuity_store, "bob", holder=_fl.GROK_FALLBACK_HOLDER, now=dt(1200.0))
    fb_epoch = int((lease_res.get("lease") or lease_res)["epoch"])
    t_1 = alert(f"bob-takeover-{fb_epoch}", "Bob took over on Grok Bot (hub unreachable)", 1200.0)
    t_2 = alert(f"bob-takeover-{fb_epoch}", "Bob took over on Grok Bot (hub unreachable)", 1210.0)
    _fl.record_slot_claim(
        continuity_store,
        role="bob",
        job="daily_note",
        slot=slot,
        holder=_fl.GROK_FALLBACK_HOLDER,
        epoch=fb_epoch,
        now=dt(1200.0),
    )
    fb_recs = [{"id": "rec-fb", "summary": "Verify RD hub", "link": "https://example.com/3296"}]
    fb_cap = build_bob_capsule(
        last_scorecard_date=scorecard_date, open_recommendations=fb_recs, next_slots=next_bob_slots(dt(1200.0))
    )
    fb_slots = [f"{k}:{v}" for k, v in sorted(fb_cap["next_slots"].items())]
    capsule_store.ingest_fallback_capsule(
        "bob",
        {
            "role": "bob",
            "epoch": fb_epoch + 1,
            "last_scorecard_date": scorecard_date,
            "open_items": fb_cap["open_items"],
            "cursors": fb_cap["cursors"],
            "next_slots": fb_slots,
        },
        now=t0 + 1500.0,
    )
    health_store.record_heartbeat("bob", epoch=fb_epoch + 1, now=t0 + 1800.0)
    resumed = health_store.record_heartbeat("bob", epoch=fb_epoch + 1, now=t0 + 2400.0)
    _fl.release_persona_lease(continuity_store, "bob", holder=_fl.GROK_FALLBACK_HOLDER, epoch=fb_epoch, now=dt(2400.0))
    b_1 = alert(f"bob-back-{fb_epoch + 1}", "Bob is back on RD", 2400.0)
    b_2 = alert(f"bob-back-{fb_epoch + 1}", "Bob is back on RD", 2410.0)
    rd_attempt = run_bob_slot(
        continuity_store,
        capsule_store,
        job="daily_note",
        slot=slot,
        scorecard_date=scorecard_date,
        notify_opts=notify_opts,
        epoch=fb_epoch + 1,
        now=dt(2420.0),
    )
    return {
        "unhealthy_transition": unhealthy.get("transition"),
        "resume_transition": resumed.get("transition"),
        "takeover_notes": int(t_1.get("status") == "sent") + int(t_2.get("status") == "sent"),
        "back_notes": int(b_1.get("status") == "sent") + int(b_2.get("status") == "sent"),
        "rd_slot_after_resume": rd_attempt,
        "final_capsule": capsule_store.load_capsule("bob"),
    }
