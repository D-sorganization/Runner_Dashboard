"""Issue Remediator RD persona role runtime, capsule schema, shadow-mode execution, and Grok
Bot fallback drill (#3704). Ports the real ``staff/roles/issue-remediator.yml`` verbatim; it has
no ``retry`` block in Repository_Management, so none is fabricated here.
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
from staff.role_capsule import RoleCapsuleStore  # noqa: E402
from staff.role_health import RoleHealthStore  # noqa: E402
from staff.role_spec import RoleSpec, read_and_parse_role_file  # noqa: E402
from staff.schedule import DEFAULT_TZ, next_fire, parse_cron  # noqa: E402

from scripts import rd_barb_notify as _notify  # noqa: E402

LA = ZoneInfo(DEFAULT_TZ)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

ROLE = "issue-remediator"

ISSUE_REMEDIATOR_SCHEDULE_ENTRIES: Mapping[str, str] = {
    "overnight_remediation": "0 23 * * *",
}

ISSUE_REMEDIATOR_TARGETS: tuple[str, ...] = ("low-complexity", "medium-complexity")
ISSUE_REMEDIATOR_SKIPS: tuple[str, ...] = (
    "board-hold",
    "external-validation-blocked",
    "claim:local",
    "P0",
    "do-not-automate",
)
ISSUE_REMEDIATOR_REPOS: tuple[str, ...] = tuple(
    "UpstreamDrift,Tools,AffineDrift,Gasification_Model,Tools_Private,Repository_Management,Runner_Dashboard,"
    "Design-Procedures,ICR-Operations,Launch-Monitor-Data,Launch-Monitor-Flight-Model-Campaign,"
    "Operations_Manual_and_Training,REE-Recovery".split(",")
)


def issue_remediator_role_data() -> dict[str, Any]:
    """Canonical staff/roles/issue-remediator.yml mapping conforming to staff/schema.json (#3704)."""
    perms = dict.fromkeys(("merge", "host_shell", "notify_user"), False)
    perms["lease"] = True
    perms["push_branch"] = True
    perms["open_pr"] = True
    return {
        "name": ROLE,
        "title": "Issue Remediator",
        "summary": ("Overnight progress on long-standing low/medium complexity open issues to shrink the backlog."),
        "playbook": "docs/fleet-issue-remediator.md",
        "prompt_template": "docs/templates/issue-remediator-report.md",
        "instructions": (
            "Overnight progress on long-standing low-to-medium complexity open issues across the fleet to shrink "
            "backlog for daytime complex work. Part of Night Watch.\n\n"
            "Lease ritual required (check_agent_claim -> post_agent_lease -> agent_communicate register). Skip "
            "Board HOLDs, claim:local, and P0 redesigns. Skip any issue whose remaining acceptance criteria need "
            "a physical measurement, lab access, field collection or a human trial: never simulate the data and "
            "never close the issue. Propose a record in the existing docs/development/planning/catalog.json "
            "where present, otherwise docs/planning/deferred-validation.json. Never create competing catalogs "
            "or reopen deferred work because evidence remains unavailable, per docs/fleet-deferred-validation.md. "
            "Source of truth: the playbook (bot-optional). No admin merges, no claim theft. Escalate tricky "
            "problems via Call the Board (one free consult; further calls notify Dieter through Barb)."
        ),
        "providers": ["claude", "codex", "antigravity"],
        "model": "default",
        "schedule": "0 23 * * *",
        "window": {"start": "22:00", "end": "06:00"},
        "repos": list(ISSUE_REMEDIATOR_REPOS),
        "scope": {
            "schedules": dict(ISSUE_REMEDIATOR_SCHEDULE_ENTRIES),
            "timezone": DEFAULT_TZ,
            "targets": list(ISSUE_REMEDIATOR_TARGETS),
            "skips": list(ISSUE_REMEDIATOR_SKIPS),
            "escalation": "docs/fleet-call-the-board.md",
            "max_lease_hours": 2,
        },
        "budget": {"usd_per_run": 5.0, "usd_per_day": 15.0},
        "permissions": perms,
        "reports_to": "night-watch",
        "holds": [],
        "surface": "dashboard",
        "persona": {
            "voice": (
                "Terse backlog worker. One issue, one plan, one lease, no speculation about the rest of the queue."
            ),
            "greeting": "Issue Remediator. Name an issue and I will tell you whether it is workable unattended.",
            "answers": [
                "whether an issue is workable unattended tonight",
                "why a given issue was skipped",
                "what an issue needs before anyone can start it",
                "what last night's pass changed on an issue",
            ],
            "defers_to": ["night-watch", "pr-remediator", "board-secretary"],
        },
        "chat": {
            "contract": "staff/prompts/_chat_contract.md",
            "providers": ["claude", "codex", "antigravity"],
            "tools": ["read_briefing", "read_run", "read_issue", "read_repo", "search_code"],
        },
    }


def write_issue_remediator_role_yaml(directory: Path) -> Path:
    """Write ``issue-remediator.yml`` into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "issue-remediator.yml"
    path.write_text(yaml.safe_dump(issue_remediator_role_data(), sort_keys=False), encoding="utf-8")
    return path


def load_issue_remediator_role_spec(directory: Path, *, raw: bool = False) -> Any:
    """Parse ``issue-remediator.yml`` from ``directory`` into a ``RoleSpec`` (or raw dict when ``raw=True``)."""
    path = directory / "issue-remediator.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if raw else read_and_parse_role_file(path)


def next_issue_remediator_slots(after: datetime, spec: RoleSpec | None = None) -> dict[str, str]:
    """Compute the next fire timestamp in America/Los_Angeles for each Issue Remediator schedule entry."""
    schedules = (
        dict(spec.scope.get("schedules") or ISSUE_REMEDIATOR_SCHEDULE_ENTRIES)
        if spec is not None and isinstance(spec.scope, dict)
        else dict(ISSUE_REMEDIATOR_SCHEDULE_ENTRIES)
    )
    return {job: next_fire(parse_cron(expr), after, tz=DEFAULT_TZ).isoformat() for job, expr in schedules.items()}


def build_issue_remediator_capsule(
    *,
    last_run_date: str,
    remediated_issues: Sequence[Mapping[str, Any]],
    next_slots: Mapping[str, str],
    cursors: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an Issue Remediator capsule payload with the run date, remediated issues, and next slots."""
    items = [
        {
            "id": str(e.get("id") or f"issue-{i + 1}").strip(),
            "outcome": str(e.get("outcome") or "skipped").strip(),
            "summary": str(e.get("summary") or "").strip(),
            "link": str(e.get("link") or "").strip(),
        }
        for i, e in enumerate(remediated_issues)
    ]
    date_s = str(last_run_date).strip()
    return {
        "last_scorecard_date": date_s,
        "remediated_issues": items,
        "open_items": [{"id": e["id"], "title": e["summary"], "url": e["link"]} for e in items],
        "cursors": dict(cursors or {"last_scorecard_date": date_s}),
        "next_slots": dict(next_slots),
    }


def validate_issue_remediator_capsule(payload: Mapping[str, Any]) -> list[str]:
    """Return contract problems for an Issue Remediator capsule payload (empty list when valid)."""
    problems: list[str] = []
    if not _DATE_RE.match(str(payload.get("last_scorecard_date") or "").strip()):
        problems.append("last_scorecard_date must be YYYY-MM-DD")
    items = payload.get("remediated_issues")
    if not isinstance(items, list):
        problems.append("remediated_issues must be a list")
    else:
        for i, item in enumerate(items):
            if not isinstance(item, Mapping) or not str(item.get("summary") or "").strip():
                problems.append(f"remediated_issues[{i}].summary must be non-empty")
                continue
            outcomes = ("progressed", "closed", "deferred", "escalated", "skipped")
            if item.get("outcome") not in outcomes:
                problems.append(f"remediated_issues[{i}].outcome must be one of {outcomes}")
            if not str(item.get("link") or "").strip().startswith(("https://", "http://")):
                problems.append(f"remediated_issues[{i}].link must be a URL")
    slots = payload.get("next_slots")
    if not isinstance(slots, Mapping) or not set(ISSUE_REMEDIATOR_SCHEDULE_ENTRIES).issubset(set(slots)):
        problems.append(f"next_slots must include {sorted(ISSUE_REMEDIATOR_SCHEDULE_ENTRIES)}")
    return problems


def save_issue_remediator_capsule(
    store: RoleCapsuleStore, payload: Mapping[str, Any], *, epoch: int = 1, now: float | None = None
) -> dict[str, Any]:
    """Validate Issue Remediator's capsule schema and persist + mirror via RoleCapsuleStore."""
    if problems := validate_issue_remediator_capsule(payload):
        raise ValueError(f"Invalid Issue Remediator capsule: {'; '.join(problems)}")
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


def run_issue_remediator_slot(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    *,
    job: str,
    slot: str,
    run_date: str,
    notify_opts: Mapping[str, Any],
    summary: str = "Issue Remediator overnight pass",
    body: str = "",
    remediated_issues: Sequence[Mapping[str, Any]] = (),
    holder: str = "rd:desk",
    epoch: int = 1,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute one scheduled Issue Remediator slot unless fallback lease is active or already run."""
    clock = now or datetime.now(LA)
    key = _fl.slot_ledger_key(ROLE, job, slot)
    if _fl.is_scheduler_blocked_by_fallback(continuity_store, ROLE, now=clock):
        return {"status": "skipped", "reason": "fallback_lease_held", "slot_key": key}
    if _fl.should_skip_slot(continuity_store, role=ROLE, job=job, slot=slot):
        return {"status": "skipped", "reason": "slot_already_executed", "slot_key": key}
    _fl.record_slot_claim(continuity_store, role=ROLE, job=job, slot=slot, holder=holder, epoch=epoch, now=clock)
    payload = build_issue_remediator_capsule(
        last_run_date=run_date,
        remediated_issues=remediated_issues,
        next_slots=next_issue_remediator_slots(clock),
    )
    saved = save_issue_remediator_capsule(capsule_store, payload, epoch=epoch, now=clock.timestamp())
    msg = {
        "kind": "update",
        "urgency": "normal",
        "summary": summary,
        "body": body,
        "dedupe_key": f"{ROLE}-{job}-{slot}",
    }
    res = _notify.persona_notify(ROLE, msg, **notify_opts, clock=clock.timestamp)
    return {"status": "executed", "slot_key": key, "capsule": saved, "notify": res}


def run_issue_remediator_fallback_drill(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    health_store: RoleHealthStore,
    *,
    slot: str,
    run_date: str,
    notify_opts: Mapping[str, Any],
    base_time: datetime | None = None,
) -> dict[str, Any]:
    """Run the staging fallback drill: hub down -> Grok takeover -> overnight pass -> resume -> RD skip."""
    t0 = (base_time or datetime(2026, 11, 3, 23, 50, tzinfo=LA)).timestamp()
    dt = lambda offset: datetime.fromtimestamp(t0 + offset, tz=LA)  # noqa: E731

    def alert(key: str, summary: str, offset: float) -> dict[str, Any]:
        env = {"kind": "alert", "urgency": "high", "summary": summary, "body": summary, "dedupe_key": key}
        return _notify.persona_notify(
            ROLE, {**env, "recommended_action": "Monitor RD hub."}, **notify_opts, clock=lambda: t0 + offset
        )

    health_store.record_heartbeat(ROLE, epoch=1, now=t0)
    health_store.record_hub_probe(ROLE, ok=False, now=t0 + 600.0)
    unhealthy = health_store.record_hub_probe(ROLE, ok=False, now=t0 + 1200.0)
    init_cap = build_issue_remediator_capsule(
        last_run_date=run_date, remediated_issues=[], next_slots=next_issue_remediator_slots(dt(0))
    )
    save_issue_remediator_capsule(capsule_store, init_cap, epoch=1, now=t0)
    lease_res = _fl.acquire_persona_lease(continuity_store, ROLE, holder=_fl.GROK_FALLBACK_HOLDER, now=dt(1200.0))
    fb_epoch = int((lease_res.get("lease") or lease_res)["epoch"])
    msg = "Issue Remediator took over on Grok Bot"
    takeovers = [alert(f"{ROLE}-takeover-{fb_epoch}", msg, o) for o in (1200.0, 1210.0)]
    _fl.record_slot_claim(
        continuity_store,
        role=ROLE,
        job="overnight_remediation",
        slot=slot,
        holder=_fl.GROK_FALLBACK_HOLDER,
        epoch=fb_epoch,
        now=dt(1200.0),
    )
    fb_items = [
        {"id": "issue-fb", "outcome": "skipped", "summary": "Verify RD hub", "link": "https://example.com/3704"}
    ]
    fb_cap = build_issue_remediator_capsule(
        last_run_date=run_date, remediated_issues=fb_items, next_slots=next_issue_remediator_slots(dt(1200.0))
    )
    fb_slots = [f"{k}:{v}" for k, v in sorted(fb_cap["next_slots"].items())]
    capsule_store.ingest_fallback_capsule(
        ROLE,
        {
            "role": ROLE,
            "epoch": fb_epoch + 1,
            "last_scorecard_date": run_date,
            "open_items": fb_cap["open_items"],
            "cursors": fb_cap["cursors"],
            "next_slots": fb_slots,
        },
        now=t0 + 1500.0,
    )
    health_store.record_heartbeat(ROLE, epoch=fb_epoch + 1, now=t0 + 1800.0)
    resumed = health_store.record_heartbeat(ROLE, epoch=fb_epoch + 1, now=t0 + 2400.0)
    _fl.release_persona_lease(continuity_store, ROLE, holder=_fl.GROK_FALLBACK_HOLDER, epoch=fb_epoch, now=dt(2400.0))
    backs = [alert(f"{ROLE}-back-{fb_epoch + 1}", "Issue Remediator is back on RD", o) for o in (2400.0, 2410.0)]
    rd_attempt = run_issue_remediator_slot(
        continuity_store,
        capsule_store,
        job="overnight_remediation",
        slot=slot,
        run_date=run_date,
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
