"""Fleet Librarian RD persona role runtime, capsule schema, shadow-mode execution, and Grok
Bot fallback drill (#3704). Ports the real ``staff/roles/librarian.yml`` verbatim; it has a
single-provider (``claude``) chain, so no ``PERSONA_ROLE_CHAINS`` failover entry is added.
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

ROLE = "librarian"

LIBRARIAN_SCHEDULE_ENTRIES: Mapping[str, str] = {
    "weekly_lesson_review": "0 10 * * 5",
}

LIBRARIAN_OUTCOMES: tuple[str, ...] = ("accepted", "quarantined", "retired", "merged")
LIBRARIAN_REPOS: tuple[str, ...] = ("Repository_Management",)


def librarian_role_data() -> dict[str, Any]:
    """Canonical staff/roles/librarian.yml mapping conforming to staff/schema.json (#3704)."""
    perms = dict.fromkeys(("merge", "host_shell", "notify_user"), False)
    perms["lease"] = True
    perms["push_branch"] = True
    perms["open_pr"] = True
    return {
        "name": ROLE,
        "title": "Fleet Librarian",
        "summary": (
            "Archivist for the reference library and steward of fleet context: manages reference "
            "catalog, weekly lesson review queue, gap reports, and fleet-rules promotion."
        ),
        "playbook": "docs/fleet-librarian.md",
        "prompt_template": None,
        "instructions": (
            "Archivist for the reference library and steward of fleet context across D-sorganization "
            "projects. Maintains the Alexandria inventory in data/library_inventory.yaml and the "
            "Google Drive reference catalog, enforcing the `Author - Title` naming standard. Never "
            "modifies Google Drive files directly; no Google credentials needed. Stewards fleet "
            "context packs and shared lesson memory via the Runner Dashboard API: runs a weekly "
            "lesson review queue (accept only lessons passing deterministic conflict/policy checks "
            "against FLEET.md, quarantine conflicts with an explicit reason, re-verify or retire at "
            "expires_at), opens one weekly Repository_Management PR promoting accepted lessons into "
            "fleet-rules/, and publishes a weekly gap report on stale context packs, AGENTS.md "
            "version lag, lessons nearing expiry, and unavailable Layer 4 library sources. Answers "
            "on-request citation queries with exact ids and canonical filenames. Reports to the Board "
            "with Barb as the operator front door; coordinates with board-secretary on meeting "
            "cadence, fleet-rules promotion, and board-facing gap reports."
        ),
        "providers": ["claude"],
        "model": "default",
        "schedule": "0 10 * * 5",
        "window": None,
        "repos": list(LIBRARIAN_REPOS),
        "scope": {
            "schedules": dict(LIBRARIAN_SCHEDULE_ENTRIES),
            "timezone": DEFAULT_TZ,
            "inventory": "data/library_inventory.yaml",
            "naming": "Author - Title",
            "connector": "google-drive",
            "review_cadence": "weekly",
            "promotion_target": "fleet-rules/",
            "lesson_ttl_days_default": 30,
            "lesson_ttl_days_max": 90,
            "conflict_action": "quarantine",
            "gap_report": "weekly",
        },
        "budget": {"usd_per_run": 2.0, "usd_per_day": 2.0},
        "permissions": perms,
        "reports_to": "barb",
        "holds": [],
        "surface": "dashboard",
        "persona": {
            "voice": "Archivist's precision. Exact filenames, exact ids, no paraphrase of a title or rule.",
            "greeting": (
                "Librarian. Ask me whether the library holds a reference, or check the status of "
                "fleet context packs and lessons."
            ),
            "answers": [
                "whether the library already holds a reference",
                "the canonical filename and inventory id for an entry",
                "which entries break the naming standard",
                "what the last inventory audit found",
                "the status of proposed, accepted, and quarantined fleet lessons",
                "per-repo AGENTS.md version lag and pack freshness",
            ],
            "defers_to": ["research-scout", "fleet-critic"],
        },
        "chat": {
            "contract": "staff/prompts/_chat_contract.md",
            "providers": ["claude"],
            "tools": ["read_repo", "search_code"],
        },
    }


def write_librarian_role_yaml(directory: Path) -> Path:
    """Write ``librarian.yml`` into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "librarian.yml"
    path.write_text(yaml.safe_dump(librarian_role_data(), sort_keys=False), encoding="utf-8")
    return path


def load_librarian_role_spec(directory: Path, *, raw: bool = False) -> Any:
    """Parse ``librarian.yml`` from ``directory`` into a ``RoleSpec`` (or raw dict when ``raw=True``)."""
    path = directory / "librarian.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if raw else read_and_parse_role_file(path)


def next_librarian_slots(after: datetime, spec: RoleSpec | None = None) -> dict[str, str]:
    """Compute the next fire timestamp in America/Los_Angeles for each Librarian schedule entry."""
    schedules = (
        dict(spec.scope.get("schedules") or LIBRARIAN_SCHEDULE_ENTRIES)
        if spec is not None and isinstance(spec.scope, dict)
        else dict(LIBRARIAN_SCHEDULE_ENTRIES)
    )
    return {job: next_fire(parse_cron(expr), after, tz=DEFAULT_TZ).isoformat() for job, expr in schedules.items()}


def build_librarian_capsule(
    *,
    last_run_date: str,
    lesson_decisions: Sequence[Mapping[str, Any]],
    next_slots: Mapping[str, str],
    cursors: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Librarian capsule payload with the run date, lesson decisions, and next slots."""
    items = [
        {
            "id": str(e.get("id") or f"lesson-{i + 1}").strip(),
            "outcome": str(e.get("outcome") or "quarantined").strip(),
            "summary": str(e.get("summary") or "").strip(),
            "link": str(e.get("link") or "").strip(),
        }
        for i, e in enumerate(lesson_decisions)
    ]
    date_s = str(last_run_date).strip()
    return {
        "last_scorecard_date": date_s,
        "lesson_decisions": items,
        "open_items": [{"id": e["id"], "title": e["summary"], "url": e["link"]} for e in items],
        "cursors": dict(cursors or {"last_scorecard_date": date_s}),
        "next_slots": dict(next_slots),
    }


def validate_librarian_capsule(payload: Mapping[str, Any]) -> list[str]:
    """Return contract problems for a Librarian capsule payload (empty list when valid)."""
    problems: list[str] = []
    if not _DATE_RE.match(str(payload.get("last_scorecard_date") or "").strip()):
        problems.append("last_scorecard_date must be YYYY-MM-DD")
    items = payload.get("lesson_decisions")
    if not isinstance(items, list):
        problems.append("lesson_decisions must be a list")
    else:
        for i, item in enumerate(items):
            if not isinstance(item, Mapping) or not str(item.get("summary") or "").strip():
                problems.append(f"lesson_decisions[{i}].summary must be non-empty")
                continue
            if item.get("outcome") not in LIBRARIAN_OUTCOMES:
                problems.append(f"lesson_decisions[{i}].outcome must be one of {LIBRARIAN_OUTCOMES}")
            if not str(item.get("link") or "").strip().startswith(("https://", "http://")):
                problems.append(f"lesson_decisions[{i}].link must be a URL")
    slots = payload.get("next_slots")
    if not isinstance(slots, Mapping) or not set(LIBRARIAN_SCHEDULE_ENTRIES).issubset(set(slots)):
        problems.append(f"next_slots must include {sorted(LIBRARIAN_SCHEDULE_ENTRIES)}")
    return problems


def save_librarian_capsule(
    store: RoleCapsuleStore, payload: Mapping[str, Any], *, epoch: int = 1, now: float | None = None
) -> dict[str, Any]:
    """Validate Librarian's capsule schema and persist + mirror via RoleCapsuleStore."""
    if problems := validate_librarian_capsule(payload):
        raise ValueError(f"Invalid Librarian capsule: {'; '.join(problems)}")
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


def run_librarian_slot(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    *,
    job: str,
    slot: str,
    run_date: str,
    notify_opts: Mapping[str, Any],
    summary: str = "Librarian weekly lesson review",
    body: str = "",
    lesson_decisions: Sequence[Mapping[str, Any]] = (),
    holder: str = "rd:desk",
    epoch: int = 1,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute one scheduled Librarian slot unless fallback lease is active or already run."""
    clock = now or datetime.now(LA)
    key = _fl.slot_ledger_key(ROLE, job, slot)
    if _fl.is_scheduler_blocked_by_fallback(continuity_store, ROLE, now=clock):
        return {"status": "skipped", "reason": "fallback_lease_held", "slot_key": key}
    if _fl.should_skip_slot(continuity_store, role=ROLE, job=job, slot=slot):
        return {"status": "skipped", "reason": "slot_already_executed", "slot_key": key}
    _fl.record_slot_claim(continuity_store, role=ROLE, job=job, slot=slot, holder=holder, epoch=epoch, now=clock)
    payload = build_librarian_capsule(
        last_run_date=run_date,
        lesson_decisions=lesson_decisions,
        next_slots=next_librarian_slots(clock),
    )
    saved = save_librarian_capsule(capsule_store, payload, epoch=epoch, now=clock.timestamp())
    msg = {
        "kind": "update",
        "urgency": "normal",
        "summary": summary,
        "body": body,
        "dedupe_key": f"{ROLE}-{job}-{slot}",
    }
    res = _notify.persona_notify(ROLE, msg, **notify_opts, clock=clock.timestamp)
    return {"status": "executed", "slot_key": key, "capsule": saved, "notify": res}


def run_librarian_fallback_drill(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    health_store: RoleHealthStore,
    *,
    slot: str,
    run_date: str,
    notify_opts: Mapping[str, Any],
    base_time: datetime | None = None,
) -> dict[str, Any]:
    """Run the staging fallback drill: hub down -> Grok takeover -> weekly pass -> resume -> RD skip."""
    t0 = (base_time or datetime(2026, 11, 6, 10, 0, tzinfo=LA)).timestamp()
    dt = lambda offset: datetime.fromtimestamp(t0 + offset, tz=LA)  # noqa: E731

    def alert(key: str, summary: str, offset: float) -> dict[str, Any]:
        env = {"kind": "alert", "urgency": "high", "summary": summary, "body": summary, "dedupe_key": key}
        return _notify.persona_notify(
            ROLE, {**env, "recommended_action": "Monitor RD hub."}, **notify_opts, clock=lambda: t0 + offset
        )

    health_store.record_heartbeat(ROLE, epoch=1, now=t0)
    health_store.record_hub_probe(ROLE, ok=False, now=t0 + 600.0)
    unhealthy = health_store.record_hub_probe(ROLE, ok=False, now=t0 + 1200.0)
    init_cap = build_librarian_capsule(
        last_run_date=run_date, lesson_decisions=[], next_slots=next_librarian_slots(dt(0))
    )
    save_librarian_capsule(capsule_store, init_cap, epoch=1, now=t0)
    lease_res = _fl.acquire_persona_lease(continuity_store, ROLE, holder=_fl.GROK_FALLBACK_HOLDER, now=dt(1200.0))
    fb_epoch = int((lease_res.get("lease") or lease_res)["epoch"])
    msg = "Librarian took over on Grok Bot"
    takeovers = [alert(f"{ROLE}-takeover-{fb_epoch}", msg, o) for o in (1200.0, 1210.0)]
    _fl.record_slot_claim(
        continuity_store,
        role=ROLE,
        job="weekly_lesson_review",
        slot=slot,
        holder=_fl.GROK_FALLBACK_HOLDER,
        epoch=fb_epoch,
        now=dt(1200.0),
    )
    fb_items = [
        {"id": "lesson-fb", "outcome": "quarantined", "summary": "Verify RD hub", "link": "https://example.com/3704"}
    ]
    fb_cap = build_librarian_capsule(
        last_run_date=run_date, lesson_decisions=fb_items, next_slots=next_librarian_slots(dt(1200.0))
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
    backs = [alert(f"{ROLE}-back-{fb_epoch + 1}", "Librarian is back on RD", o) for o in (2400.0, 2410.0)]
    rd_attempt = run_librarian_slot(
        continuity_store,
        capsule_store,
        job="weekly_lesson_review",
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
