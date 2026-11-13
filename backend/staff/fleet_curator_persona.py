"""Fleet Curator RD persona role runtime, capsule schema, shadow-mode execution, and Grok
Bot fallback drill (#3704). Ports the real ``staff/roles/fleet-curator.yml`` verbatim.
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

ROLE = "fleet-curator"

FLEET_CURATOR_SCHEDULE_ENTRIES: Mapping[str, str] = {
    "weekly_untracked_review": "0 6 * * 3",
}

FLEET_CURATOR_DISPOSITIONS: tuple[str, ...] = ("track", "implement", "integrate", "deprecate")
FLEET_CURATOR_REPOS: tuple[str, ...] = ("Repository_Management", "Runner_Dashboard")


def fleet_curator_role_data() -> dict[str, Any]:
    """Canonical staff/roles/fleet-curator.yml mapping conforming to staff/schema.json (#3704)."""
    perms = dict.fromkeys(("merge", "host_shell", "notify_user"), False)
    perms["lease"] = True
    perms["push_branch"] = True
    perms["open_pr"] = True
    return {
        "name": ROLE,
        "title": "Fleet Curator",
        "summary": (
            "Finds fleet work nothing tracks (orphaned issues/PRs, charter-less or unregistered "
            "repos, stray branches and plans), decides where each belongs, and keeps "
            "docs/fleet-status.md current."
        ),
        "playbook": "docs/fleet-curator.md",
        "prompt_template": None,
        "instructions": (
            "Keep every piece of fleet work accounted for somewhere a project steward or the "
            "Board will see it. Reads GET /api/projects/untracked and GET /api/projects from the "
            "Runner Dashboard, decides one disposition per untracked item (track, implement, "
            "integrate, deprecate) with recorded evidence, opens docs-only PRs for charter-less "
            "or unregistered repos, regenerates docs/fleet-status.md, and submits cross-repo "
            "integrate/deprecate recommendations to the Board via submit_proposal, at most "
            "proposals_per_run. Never deletes branches, closes issues or changes priority tiers. "
            "Interim: reports to barb until the first Board meeting is recorded in "
            "docs/board-meetings/pathway-log.md (RM#1766 Phase 0); then reverts to board-secretary."
        ),
        "providers": ["claude", "codex", "antigravity"],
        "model": "default",
        "schedule": "0 6 * * 3",
        "window": None,
        "repos": list(FLEET_CURATOR_REPOS),
        "scope": {
            "schedules": dict(FLEET_CURATOR_SCHEDULE_ENTRIES),
            "timezone": DEFAULT_TZ,
            "dispositions": list(FLEET_CURATOR_DISPOSITIONS),
            "pr_kind": "docs-only",
            "proposals_per_run": 5,
        },
        "budget": {"usd_per_run": 3.0, "usd_per_day": 3.0},
        "permissions": perms,
        "reports_to": "barb",
        "holds": [],
        "surface": "dashboard",
        "persona": {
            "voice": "Fleet curator. Names the item, where it lives, where it belongs and why, with the evidence.",
            "greeting": (
                "Fleet Curator. Ask me what work nobody is tracking, or where a stray issue, branch or repo belongs."
            ),
            "answers": [
                "which open issues and PRs no charter feature accounts for",
                "which repositories have no charter or are missing from the Projects tab",
                "whether a piece of work should be tracked, implemented, integrated or deprecated",
                "what the fleet status report says about a project",
            ],
            "defers_to": ["project-steward", "board-secretary", "sanitation"],
        },
        "chat": {
            "contract": "staff/prompts/_chat_contract.md",
            "providers": ["claude", "codex", "antigravity"],
            "tools": ["read_briefing", "read_priorities", "read_issue", "read_repo", "search_code"],
        },
    }


def write_fleet_curator_role_yaml(directory: Path) -> Path:
    """Write ``fleet-curator.yml`` into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "fleet-curator.yml"
    path.write_text(yaml.safe_dump(fleet_curator_role_data(), sort_keys=False), encoding="utf-8")
    return path


def load_fleet_curator_role_spec(directory: Path, *, raw: bool = False) -> Any:
    """Parse ``fleet-curator.yml`` from ``directory`` into a ``RoleSpec`` (or raw dict when ``raw=True``)."""
    path = directory / "fleet-curator.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if raw else read_and_parse_role_file(path)


def next_fleet_curator_slots(after: datetime, spec: RoleSpec | None = None) -> dict[str, str]:
    """Compute the next fire timestamp in America/Los_Angeles for each Fleet Curator schedule entry."""
    schedules = (
        dict(spec.scope.get("schedules") or FLEET_CURATOR_SCHEDULE_ENTRIES)
        if spec is not None and isinstance(spec.scope, dict)
        else dict(FLEET_CURATOR_SCHEDULE_ENTRIES)
    )
    return {job: next_fire(parse_cron(expr), after, tz=DEFAULT_TZ).isoformat() for job, expr in schedules.items()}


def build_fleet_curator_capsule(
    *,
    last_review_date: str,
    dispositions: Sequence[Mapping[str, Any]],
    next_slots: Mapping[str, str],
    cursors: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Fleet Curator capsule payload with the review date, dispositions, and next slots."""
    items = [
        {
            "id": str(e.get("id") or f"item-{i + 1}").strip(),
            "disposition": str(e.get("disposition") or "track").strip(),
            "summary": str(e.get("summary") or "").strip(),
            "link": str(e.get("link") or "").strip(),
        }
        for i, e in enumerate(dispositions)
    ]
    date_s = str(last_review_date).strip()
    return {
        "last_scorecard_date": date_s,
        "dispositions": items,
        "open_items": [{"id": e["id"], "title": e["summary"], "url": e["link"]} for e in items],
        "cursors": dict(cursors or {"last_scorecard_date": date_s}),
        "next_slots": dict(next_slots),
    }


def validate_fleet_curator_capsule(payload: Mapping[str, Any]) -> list[str]:
    """Return contract problems for a Fleet Curator capsule payload (empty list when valid)."""
    problems: list[str] = []
    if not _DATE_RE.match(str(payload.get("last_scorecard_date") or "").strip()):
        problems.append("last_scorecard_date must be YYYY-MM-DD")
    items = payload.get("dispositions")
    if not isinstance(items, list):
        problems.append("dispositions must be a list")
    else:
        for i, item in enumerate(items):
            if not isinstance(item, Mapping) or not str(item.get("summary") or "").strip():
                problems.append(f"dispositions[{i}].summary must be non-empty")
                continue
            if item.get("disposition") not in FLEET_CURATOR_DISPOSITIONS:
                problems.append(f"dispositions[{i}].disposition must be one of {FLEET_CURATOR_DISPOSITIONS}")
            if not str(item.get("link") or "").strip().startswith(("https://", "http://")):
                problems.append(f"dispositions[{i}].link must be a URL")
    slots = payload.get("next_slots")
    if not isinstance(slots, Mapping) or not set(FLEET_CURATOR_SCHEDULE_ENTRIES).issubset(set(slots)):
        problems.append(f"next_slots must include {sorted(FLEET_CURATOR_SCHEDULE_ENTRIES)}")
    return problems


def save_fleet_curator_capsule(
    store: RoleCapsuleStore, payload: Mapping[str, Any], *, epoch: int = 1, now: float | None = None
) -> dict[str, Any]:
    """Validate Fleet Curator's capsule schema and persist + mirror via RoleCapsuleStore."""
    if problems := validate_fleet_curator_capsule(payload):
        raise ValueError(f"Invalid Fleet Curator capsule: {'; '.join(problems)}")
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


def run_fleet_curator_slot(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    *,
    job: str,
    slot: str,
    review_date: str,
    notify_opts: Mapping[str, Any],
    summary: str = "Fleet Curator weekly untracked-work review",
    body: str = "",
    dispositions: Sequence[Mapping[str, Any]] = (),
    holder: str = "rd:desk",
    epoch: int = 1,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute one scheduled Fleet Curator slot unless fallback lease is active or already run."""
    clock = now or datetime.now(LA)
    key = _fl.slot_ledger_key(ROLE, job, slot)
    if _fl.is_scheduler_blocked_by_fallback(continuity_store, ROLE, now=clock):
        return {"status": "skipped", "reason": "fallback_lease_held", "slot_key": key}
    if _fl.should_skip_slot(continuity_store, role=ROLE, job=job, slot=slot):
        return {"status": "skipped", "reason": "slot_already_executed", "slot_key": key}
    _fl.record_slot_claim(continuity_store, role=ROLE, job=job, slot=slot, holder=holder, epoch=epoch, now=clock)
    payload = build_fleet_curator_capsule(
        last_review_date=review_date,
        dispositions=dispositions,
        next_slots=next_fleet_curator_slots(clock),
    )
    saved = save_fleet_curator_capsule(capsule_store, payload, epoch=epoch, now=clock.timestamp())
    msg = {
        "kind": "update",
        "urgency": "normal",
        "summary": summary,
        "body": body,
        "dedupe_key": f"{ROLE}-{job}-{slot}",
    }
    res = _notify.persona_notify(ROLE, msg, **notify_opts, clock=clock.timestamp)
    return {"status": "executed", "slot_key": key, "capsule": saved, "notify": res}


def run_fleet_curator_fallback_drill(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    health_store: RoleHealthStore,
    *,
    slot: str,
    review_date: str,
    notify_opts: Mapping[str, Any],
    base_time: datetime | None = None,
) -> dict[str, Any]:
    """Run the staging fallback drill: hub down -> Grok takeover -> weekly review -> resume -> RD skip."""
    t0 = (base_time or datetime(2026, 11, 4, 6, 0, tzinfo=LA)).timestamp()
    dt = lambda offset: datetime.fromtimestamp(t0 + offset, tz=LA)  # noqa: E731

    def alert(key: str, summary: str, offset: float) -> dict[str, Any]:
        env = {"kind": "alert", "urgency": "high", "summary": summary, "body": summary, "dedupe_key": key}
        return _notify.persona_notify(
            ROLE, {**env, "recommended_action": "Monitor RD hub."}, **notify_opts, clock=lambda: t0 + offset
        )

    health_store.record_heartbeat(ROLE, epoch=1, now=t0)
    health_store.record_hub_probe(ROLE, ok=False, now=t0 + 600.0)
    unhealthy = health_store.record_hub_probe(ROLE, ok=False, now=t0 + 1200.0)
    init_cap = build_fleet_curator_capsule(
        last_review_date=review_date, dispositions=[], next_slots=next_fleet_curator_slots(dt(0))
    )
    save_fleet_curator_capsule(capsule_store, init_cap, epoch=1, now=t0)
    lease_res = _fl.acquire_persona_lease(continuity_store, ROLE, holder=_fl.GROK_FALLBACK_HOLDER, now=dt(1200.0))
    fb_epoch = int((lease_res.get("lease") or lease_res)["epoch"])
    msg = "Fleet Curator took over on Grok Bot"
    takeovers = [alert(f"{ROLE}-takeover-{fb_epoch}", msg, o) for o in (1200.0, 1210.0)]
    _fl.record_slot_claim(
        continuity_store,
        role=ROLE,
        job="weekly_untracked_review",
        slot=slot,
        holder=_fl.GROK_FALLBACK_HOLDER,
        epoch=fb_epoch,
        now=dt(1200.0),
    )
    fb_items = [
        {"id": "item-fb", "disposition": "track", "summary": "Verify RD hub", "link": "https://example.com/3704"}
    ]
    fb_cap = build_fleet_curator_capsule(
        last_review_date=review_date, dispositions=fb_items, next_slots=next_fleet_curator_slots(dt(1200.0))
    )
    fb_slots = [f"{k}:{v}" for k, v in sorted(fb_cap["next_slots"].items())]
    capsule_store.ingest_fallback_capsule(
        ROLE,
        {
            "role": ROLE,
            "epoch": fb_epoch + 1,
            "last_scorecard_date": review_date,
            "open_items": fb_cap["open_items"],
            "cursors": fb_cap["cursors"],
            "next_slots": fb_slots,
        },
        now=t0 + 1500.0,
    )
    health_store.record_heartbeat(ROLE, epoch=fb_epoch + 1, now=t0 + 1800.0)
    resumed = health_store.record_heartbeat(ROLE, epoch=fb_epoch + 1, now=t0 + 2400.0)
    _fl.release_persona_lease(continuity_store, ROLE, holder=_fl.GROK_FALLBACK_HOLDER, epoch=fb_epoch, now=dt(2400.0))
    backs = [alert(f"{ROLE}-back-{fb_epoch + 1}", "Fleet Curator is back on RD", o) for o in (2400.0, 2410.0)]
    rd_attempt = run_fleet_curator_slot(
        continuity_store,
        capsule_store,
        job="weekly_untracked_review",
        slot=slot,
        review_date=review_date,
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
