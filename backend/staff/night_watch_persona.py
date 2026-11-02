"""Night Watch RD persona role runtime, capsule schema, shadow-mode execution, and Grok Bot
fallback drill (#3704, next slice of the epic #3695 cutover-tracking checklist). Ports the
real ``staff/roles/night-watch.yml`` from Repository_Management, matching the earlier #3704
slices; its permissions match its own instructions, so there is no conflict to flag here.
"""

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

ROLE = "night-watch"

NIGHT_WATCH_SCHEDULE_ENTRIES: Mapping[str, str] = {
    "overnight_triage": "0 22 * * *",
}

# Real staff/roles/night-watch.yml: 3 providers, "model: default" (no per-step override).
NIGHT_WATCH_FAILOVER_CHAIN: tuple[FailoverStep, ...] = (
    FailoverStep("claude", None),
    FailoverStep("codex", None),
    FailoverStep("antigravity", None),
)

NIGHT_WATCH_SUB_ROLES: tuple[str, ...] = ("issue-remediator", "pr-remediator", "sanitation", "cartographer")
NIGHT_WATCH_REPOS: tuple[str, ...] = tuple(
    "UpstreamDrift,Tools,AffineDrift,Gasification_Model,Tools_Private,Repository_Management,Runner_Dashboard".split(",")
)


def night_watch_role_data() -> dict[str, Any]:
    """Canonical staff/roles/night-watch.yml mapping conforming to staff/schema.json (#3704)."""
    perms = dict.fromkeys(("merge", "host_shell", "notify_user"), False)
    perms["lease"] = True
    perms["push_branch"] = True
    perms["open_pr"] = True
    chain = [{"provider": s.provider, **({"model": s.model} if s.model else {})} for s in NIGHT_WATCH_FAILOVER_CHAIN]
    return {
        "name": ROLE,
        "title": "Night Watch",
        "summary": "Overnight triage and hygiene: simple issues/PRs, worktree cleanup, docs currency.",
        "playbook": "docs/fleet-night-watch.md",
        "prompt_template": "docs/templates/night-watch-report.md",
        "instructions": (
            "Overnight triage and hygiene for D-sorganization fleets: simple issues/PRs, "
            "deprecate/clean up worktrees and completed work, docs currency and repo compliance. "
            "Runner health and fleet operations are owned by Fleet Maintenance. Safety-first. "
            "Source of truth is Repository_Management docs, not any bot memory. Does not steal "
            "claims, admin-merge or file Board remediation floods. Coordinates with Orchestrator; "
            "hands complex work to PMs/Fleet-Dispatcher. Issue Remediator, PR Remediator, "
            "Sanitation and Cartographer run under Night Watch."
        ),
        "providers": ["claude", "codex", "antigravity"],
        "model": "default",
        "schedule": "0 22 * * *",
        "window": {"start": "22:00", "end": "06:00"},
        "repos": list(NIGHT_WATCH_REPOS),
        "scope": {
            "schedules": dict(NIGHT_WATCH_SCHEDULE_ENTRIES),
            "timezone": DEFAULT_TZ,
            "sub_roles": list(NIGHT_WATCH_SUB_ROLES),
            "owns": ["overnight-triage", "worktree-hygiene"],
            "max_lease_hours": 2,
        },
        "budget": {"usd_per_run": 8.0, "usd_per_day": 8.0},
        "permissions": perms,
        "retry": {"max_attempts": 3, "failover_chain": chain},
        "reports_to": "barb",
        "holds": [],
        "surface": "dashboard",
        "persona": {
            "voice": "Duty officer. What happened, what is still broken, what it cost, in that order.",
            "greeting": "Night Watch. Ask me what the overnight pass did, or what is still red this morning.",
            "answers": [
                "what the overnight pass changed and what it cost",
                "what is still red and who owns it",
                "overnight triage and worktree hygiene on each host",
            ],
            "defers_to": ["issue-remediator", "pr-remediator", "sanitation", "cartographer", "maintenance"],
        },
        "chat": {
            "contract": "staff/prompts/_chat_contract.md",
            "providers": ["claude", "codex", "antigravity"],
            "tools": ["read_briefing", "read_staff_summary", "read_sessions", "read_run", "read_issue"],
        },
    }


def write_night_watch_role_yaml(directory: Path) -> Path:
    """Write ``night-watch.yml`` into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "night-watch.yml"
    path.write_text(yaml.safe_dump(night_watch_role_data(), sort_keys=False), encoding="utf-8")
    return path


def load_night_watch_role_spec(directory: Path, *, raw: bool = False) -> Any:
    """Parse ``night-watch.yml`` from ``directory`` into a ``RoleSpec`` (or raw dict when ``raw=True``)."""
    path = directory / "night-watch.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if raw else read_and_parse_role_file(path)


def next_night_watch_slots(after: datetime, spec: RoleSpec | None = None) -> dict[str, str]:
    """Compute the next fire timestamp in America/Los_Angeles for each Night Watch schedule entry."""
    schedules = (
        dict(spec.scope.get("schedules") or NIGHT_WATCH_SCHEDULE_ENTRIES)
        if spec is not None and isinstance(spec.scope, dict)
        else dict(NIGHT_WATCH_SCHEDULE_ENTRIES)
    )
    return {job: next_fire(parse_cron(expr), after, tz=DEFAULT_TZ).isoformat() for job, expr in schedules.items()}


def build_night_watch_capsule(
    *,
    last_overnight_date: str,
    triage_items: Sequence[Mapping[str, Any]],
    next_slots: Mapping[str, str],
    cursors: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Night Watch capsule payload with the overnight date, triage items, and next slots."""
    items = [
        {
            "id": str(e.get("id") or f"triage-{i + 1}").strip(),
            "summary": str(e.get("summary") or "").strip(),
            "link": str(e.get("link") or "").strip(),
            "sub_role": str(e.get("sub_role") or "").strip(),
        }
        for i, e in enumerate(triage_items)
    ]
    date_s = str(last_overnight_date).strip()
    return {
        "last_scorecard_date": date_s,
        "triage_items": items,
        "open_items": [{"id": e["id"], "title": e["summary"], "url": e["link"]} for e in items],
        "cursors": dict(cursors or {"last_scorecard_date": date_s}),
        "next_slots": dict(next_slots),
    }


def validate_night_watch_capsule(payload: Mapping[str, Any]) -> list[str]:
    """Return contract problems for a Night Watch capsule payload (empty list when valid)."""
    problems: list[str] = []
    if not _DATE_RE.match(str(payload.get("last_scorecard_date") or "").strip()):
        problems.append("last_scorecard_date must be YYYY-MM-DD")
    items = payload.get("triage_items")
    if not isinstance(items, list):
        problems.append("triage_items must be a list")
    else:
        for i, item in enumerate(items):
            if not isinstance(item, Mapping) or not str(item.get("summary") or "").strip():
                problems.append(f"triage_items[{i}].summary must be non-empty")
                continue
            if not str(item.get("link") or "").strip().startswith(("https://", "http://")):
                problems.append(f"triage_items[{i}].link must be a URL")
            if str(item.get("sub_role") or "").strip() not in NIGHT_WATCH_SUB_ROLES:
                problems.append(f"triage_items[{i}].sub_role must be one of {sorted(NIGHT_WATCH_SUB_ROLES)}")
    slots = payload.get("next_slots")
    if not isinstance(slots, Mapping) or not set(NIGHT_WATCH_SCHEDULE_ENTRIES).issubset(set(slots)):
        problems.append(f"next_slots must include {sorted(NIGHT_WATCH_SCHEDULE_ENTRIES)}")
    return problems


def save_night_watch_capsule(
    store: RoleCapsuleStore, payload: Mapping[str, Any], *, epoch: int = 1, now: float | None = None
) -> dict[str, Any]:
    """Validate Night Watch's capsule schema and persist + mirror via RoleCapsuleStore."""
    if problems := validate_night_watch_capsule(payload):
        raise ValueError(f"Invalid Night Watch capsule: {'; '.join(problems)}")
    slots_map = dict(payload["next_slots"])
    date_s = str(payload["last_scorecard_date"])
    return store.save_capsule(
        ROLE,
        epoch=epoch,
        last_scorecard_date=date_s,
        open_items=list(payload.get("open_items") or []),
        cursors={**dict(payload.get("cursors") or {}), "last_scorecard_date": date_s},
        next_slots=[f"{job}:{ts}" for job, ts in sorted(slots_map.items())],
        now=now,
    )


def run_night_watch_slot(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    *,
    job: str,
    slot: str,
    overnight_date: str,
    notify_opts: Mapping[str, Any],
    summary: str = "Night Watch overnight pass",
    body: str = "",
    triage_items: Sequence[Mapping[str, Any]] = (),
    holder: str = "rd:desk",
    epoch: int = 1,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute one scheduled Night Watch slot unless fallback lease is active or already run.

    The shadow-mode thread post is the #3295/#3296 telemetry channel (``RD_<ROLE>_THREAD_ID``);
    this slot never leases an issue or pushes a branch itself, only records the capsule.
    """
    clock = now or datetime.now(LA)
    key = _fl.slot_ledger_key(ROLE, job, slot)
    if _fl.is_scheduler_blocked_by_fallback(continuity_store, ROLE, now=clock):
        return {"status": "skipped", "reason": "fallback_lease_held", "slot_key": key}
    if _fl.should_skip_slot(continuity_store, role=ROLE, job=job, slot=slot):
        return {"status": "skipped", "reason": "slot_already_executed", "slot_key": key}
    _fl.record_slot_claim(continuity_store, role=ROLE, job=job, slot=slot, holder=holder, epoch=epoch, now=clock)
    payload = build_night_watch_capsule(
        last_overnight_date=overnight_date,
        triage_items=triage_items,
        next_slots=next_night_watch_slots(clock),
    )
    saved = save_night_watch_capsule(capsule_store, payload, epoch=epoch, now=clock.timestamp())
    msg = {
        "kind": "update",
        "urgency": "normal",
        "summary": summary,
        "body": body,
        "dedupe_key": f"{ROLE}-{job}-{slot}",
    }
    res = _notify.persona_notify(ROLE, msg, **notify_opts, clock=clock.timestamp)
    return {"status": "executed", "slot_key": key, "capsule": saved, "notify": res}


def run_night_watch_fallback_drill(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    health_store: RoleHealthStore,
    *,
    slot: str,
    overnight_date: str,
    notify_opts: Mapping[str, Any],
    base_time: datetime | None = None,
) -> dict[str, Any]:
    """Run the staging fallback drill: hub down -> Grok takeover -> overnight pass -> resume -> RD skip."""
    t0 = (base_time or datetime(2026, 11, 3, 21, 50, tzinfo=LA)).timestamp()
    dt = lambda offset: datetime.fromtimestamp(t0 + offset, tz=LA)  # noqa: E731

    def alert(key: str, summary: str, offset: float) -> dict[str, Any]:
        env = {"kind": "alert", "urgency": "high", "summary": summary, "body": summary, "dedupe_key": key}
        return _notify.persona_notify(
            ROLE, {**env, "recommended_action": "Monitor RD hub."}, **notify_opts, clock=lambda: t0 + offset
        )

    health_store.record_heartbeat(ROLE, epoch=1, now=t0)
    health_store.record_hub_probe(ROLE, ok=False, now=t0 + 600.0)
    unhealthy = health_store.record_hub_probe(ROLE, ok=False, now=t0 + 1200.0)
    init_cap = build_night_watch_capsule(
        last_overnight_date=overnight_date, triage_items=[], next_slots=next_night_watch_slots(dt(0))
    )
    save_night_watch_capsule(capsule_store, init_cap, epoch=1, now=t0)
    lease_res = _fl.acquire_persona_lease(continuity_store, ROLE, holder=_fl.GROK_FALLBACK_HOLDER, now=dt(1200.0))
    fb_epoch = int((lease_res.get("lease") or lease_res)["epoch"])
    msg = "Night Watch took over on Grok Bot"
    takeovers = [alert(f"{ROLE}-takeover-{fb_epoch}", msg, o) for o in (1200.0, 1210.0)]
    _fl.record_slot_claim(
        continuity_store,
        role=ROLE,
        job="overnight_triage",
        slot=slot,
        holder=_fl.GROK_FALLBACK_HOLDER,
        epoch=fb_epoch,
        now=dt(1200.0),
    )
    fb_items = [
        {"id": "triage-fb", "summary": "Verify RD hub", "link": "https://example.com/3704", "sub_role": "sanitation"}
    ]
    fb_cap = build_night_watch_capsule(
        last_overnight_date=overnight_date,
        triage_items=fb_items,
        next_slots=next_night_watch_slots(dt(1200.0)),
    )
    fb_slots = [f"{k}:{v}" for k, v in sorted(fb_cap["next_slots"].items())]
    capsule_store.ingest_fallback_capsule(
        ROLE,
        {
            "role": ROLE,
            "epoch": fb_epoch + 1,
            "last_scorecard_date": overnight_date,
            "open_items": fb_cap["open_items"],
            "cursors": fb_cap["cursors"],
            "next_slots": fb_slots,
        },
        now=t0 + 1500.0,
    )
    health_store.record_heartbeat(ROLE, epoch=fb_epoch + 1, now=t0 + 1800.0)
    resumed = health_store.record_heartbeat(ROLE, epoch=fb_epoch + 1, now=t0 + 2400.0)
    _fl.release_persona_lease(continuity_store, ROLE, holder=_fl.GROK_FALLBACK_HOLDER, epoch=fb_epoch, now=dt(2400.0))
    backs = [alert(f"{ROLE}-back-{fb_epoch + 1}", "Night Watch is back on RD", o) for o in (2400.0, 2410.0)]
    rd_attempt = run_night_watch_slot(
        continuity_store,
        capsule_store,
        job="overnight_triage",
        slot=slot,
        overnight_date=overnight_date,
        notify_opts=notify_opts,
        epoch=fb_epoch + 1,
        now=dt(2420.0),
    )
    return {
        "unhealthy_transition": unhealthy.get("transition"),
        "resume_transition": resumed.get("transition"),
        "takeover_notes": sum(n.get("status") == "sent" for n in takeovers),
        "back_notes": sum(n.get("status") == "sent" for n in backs),
        "rd_slot_after_resume": rd_attempt,
        "final_capsule": capsule_store.load_capsule(ROLE),
    }
