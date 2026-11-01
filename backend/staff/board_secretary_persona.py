"""Board Secretary RD persona role runtime, capsule schema, shadow-mode execution, and Grok Bot
fallback drill (#3704). Ports ``staff/roles/board-secretary.yml``; its permissions match."""

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

ROLE = "board-secretary"

BOARD_SECRETARY_SCHEDULE_ENTRIES: Mapping[str, str] = {
    "weekly_board_meeting": "0 20 * * 5",
}

# Real staff/roles/board-secretary.yml: 4 providers, "model: default" (no per-step override).
BOARD_SECRETARY_FAILOVER_CHAIN: tuple[FailoverStep, ...] = (
    FailoverStep("claude", None),
    FailoverStep("codex", None),
    FailoverStep("antigravity", None),
    FailoverStep("cursor-agent", None),
)

BOARD_SECRETARY_SEATS: tuple[str, ...] = ("alpha", "bravo", "charlie", "delta")
BOARD_SECRETARY_DISPOSITIONS: tuple[str, ...] = ("accept", "defer", "decline", "needs-info")
BOARD_SECRETARY_REPOS: tuple[str, ...] = tuple(
    "UpstreamDrift,AffineDrift,Gasification_Model,Tools,Tools_Private,Repository_Management,Runner_Dashboard,"
    "Design-Procedures,ICR-Operations,Launch-Monitor-Data,Launch-Monitor-Flight-Model-Campaign,"
    "Operations_Manual_and_Training,REE-Recovery".split(",")
)


def board_secretary_role_data() -> dict[str, Any]:
    """Canonical staff/roles/board-secretary.yml mapping conforming to staff/schema.json (#3704)."""
    perms = dict.fromkeys(("merge", "host_shell", "notify_user"), False)
    perms["lease"] = True
    perms["push_branch"] = True
    perms["open_pr"] = True
    fc = BOARD_SECRETARY_FAILOVER_CHAIN
    chain = [{"provider": s.provider, **({"model": s.model} if s.model else {})} for s in fc]
    return {
        "name": ROLE,
        "title": "Board Secretary",
        "summary": "Chairs the weekly Friday Board Meeting: seat reports, cross-review, consensus, archive.",
        "playbook": "docs/board-meeting.md",
        "prompt_template": "docs/templates/board-consensus.md",
        "instructions": (
            "Chairs the weekly Friday Board Meeting: collects seat reports from alpha/bravo/charlie/delta, "
            "runs cross-review plus ranking/consensus, assigns ONE instruction-writer for critical epics "
            "only, archives packets under docs/board-meetings/YYYY-MM-DD/, and gives every board:proposal a "
            "disposition (accept/defer/decline/needs-info). Real seats every time (recorded th_ ids); no "
            "th_ ids means single-seat. Never writes seat reports itself; coordinates with Orchestrator, "
            "Fleet-Dispatcher, Fleet-Critic and Librarian; does not steal claims or merge without green CI."
        ),
        "providers": ["claude", "codex", "antigravity", "cursor-agent"],
        "model": "default",
        "schedule": "0 20 * * 5",
        "window": None,
        "repos": list(BOARD_SECRETARY_REPOS),
        "scope": {
            "schedules": dict(BOARD_SECRETARY_SCHEDULE_ENTRIES),
            "seats": list(BOARD_SECRETARY_SEATS),
            "archive": "docs/board-meetings",
            "proposal_queue_label": "board:proposal",
            "pathway_log": "docs/board-meetings/pathway-log.md",
            "instruction_writers_per_meeting": 1,
        },
        "budget": {"usd_per_run": 10.0, "usd_per_day": 10.0},
        "permissions": perms,
        "retry": {"max_attempts": 4, "failover_chain": chain},
        "reports_to": "barb",
        "holds": [],
        "surface": "both",
        "persona": {
            "voice": "Neutral chair. Records every position, argues none of them, cites the meeting it came from.",
            "greeting": "Board Secretary. Ask me what the Board decided, or where a proposal stands.",
            "answers": [
                "what the last board meeting decided and why",
                "where a board proposal stands in the queue",
                "what is in this week's packet and which seats have reported",
            ],
            "defers_to": ["fleet-critic", "pragmatic-programmer", "research-scout", "oss-scout", "project-steward"],
        },
        "chat": {
            "contract": "staff/prompts/_chat_contract.md",
            "providers": ["claude", "codex", "antigravity", "cursor-agent"],
            "tools": ["read_briefing", "read_priorities", "read_issue", "read_repo", "search_code"],
        },
    }


def write_board_secretary_role_yaml(directory: Path) -> Path:
    """Write ``board-secretary.yml`` into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "board-secretary.yml"
    path.write_text(yaml.safe_dump(board_secretary_role_data(), sort_keys=False), encoding="utf-8")
    return path


def load_board_secretary_role_spec(directory: Path, *, raw: bool = False) -> Any:
    """Parse ``board-secretary.yml`` from ``directory`` into a ``RoleSpec`` (or raw dict when ``raw=True``)."""
    path = directory / "board-secretary.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if raw else read_and_parse_role_file(path)


def next_board_secretary_slots(after: datetime, spec: RoleSpec | None = None) -> dict[str, str]:
    """Compute the next fire timestamp in America/Los_Angeles for each Board Secretary schedule entry."""
    schedules = (
        dict(spec.scope.get("schedules") or BOARD_SECRETARY_SCHEDULE_ENTRIES)
        if spec is not None and isinstance(spec.scope, dict)
        else dict(BOARD_SECRETARY_SCHEDULE_ENTRIES)
    )
    return {job: next_fire(parse_cron(expr), after, tz=DEFAULT_TZ).isoformat() for job, expr in schedules.items()}


def build_board_secretary_capsule(
    *,
    meeting_date: str,
    seats_reported: Sequence[str],
    proposal_dispositions: Sequence[Mapping[str, Any]],
    next_slots: Mapping[str, str],
    cursors: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Board Secretary capsule payload with the meeting date, dispositions, and next slots."""
    seats = [str(s).strip() for s in seats_reported]
    keys = ("proposal_id", "disposition", "rationale")
    dispositions = [{k: str(d.get(k) or "").strip() for k in keys} for d in proposal_dispositions]
    date_s = str(meeting_date).strip()
    return {
        "last_scorecard_date": date_s,
        "seats_reported": seats,
        "proposal_dispositions": dispositions,
        "open_items": [
            {"id": d["proposal_id"], "title": f"{d['disposition']}: {d['rationale']}", "url": ""}
            for d in dispositions
            if d["disposition"] == "needs-info"
        ],
        "cursors": dict(cursors or {"last_scorecard_date": date_s}),
        "next_slots": dict(next_slots),
    }


def validate_board_secretary_capsule(payload: Mapping[str, Any]) -> list[str]:
    """Return contract problems for a Board Secretary capsule payload (empty list when valid)."""
    problems: list[str] = []
    if not _DATE_RE.match(str(payload.get("last_scorecard_date") or "").strip()):
        problems.append("last_scorecard_date must be YYYY-MM-DD")
    seats = payload.get("seats_reported")
    if not isinstance(seats, list) or not set(seats).issubset(set(BOARD_SECRETARY_SEATS)):
        problems.append(f"seats_reported must be a subset of {sorted(BOARD_SECRETARY_SEATS)}")
    dispositions = payload.get("proposal_dispositions")
    if not isinstance(dispositions, list):
        problems.append("proposal_dispositions must be a list")
    else:
        for i, d in enumerate(dispositions):
            if not isinstance(d, Mapping) or not str(d.get("proposal_id") or "").strip():
                problems.append(f"proposal_dispositions[{i}].proposal_id must be non-empty")
                continue
            if str(d.get("disposition") or "").strip() not in BOARD_SECRETARY_DISPOSITIONS:
                problems.append(f"proposal_dispositions[{i}].disposition must be one of {BOARD_SECRETARY_DISPOSITIONS}")
            if not str(d.get("rationale") or "").strip():
                problems.append(f"proposal_dispositions[{i}].rationale must be non-empty")
    slots = payload.get("next_slots")
    if not isinstance(slots, Mapping) or not set(BOARD_SECRETARY_SCHEDULE_ENTRIES).issubset(set(slots)):
        problems.append(f"next_slots must include {sorted(BOARD_SECRETARY_SCHEDULE_ENTRIES)}")
    return problems


def save_board_secretary_capsule(
    store: RoleCapsuleStore, payload: Mapping[str, Any], *, epoch: int = 1, now: float | None = None
) -> dict[str, Any]:
    """Validate Board Secretary's capsule schema and persist + mirror via RoleCapsuleStore."""
    if problems := validate_board_secretary_capsule(payload):
        raise ValueError(f"Invalid Board Secretary capsule: {'; '.join(problems)}")
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


def run_board_secretary_slot(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    *,
    job: str,
    slot: str,
    meeting_date: str,
    notify_opts: Mapping[str, Any],
    summary: str = "Board Secretary weekly meeting",
    body: str = "",
    seats_reported: Sequence[str] = (),
    proposal_dispositions: Sequence[Mapping[str, Any]] = (),
    holder: str = "rd:desk",
    epoch: int = 1,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute one scheduled Board Secretary slot unless fallback lease is active or already run.

    The shadow-mode thread post below is the #3295/#3296 telemetry channel (``RD_<ROLE>_THREAD_ID``),
    distinct from ``permissions.notify_user`` (false here) which gates direct chat notifications.
    """
    clock = now or datetime.now(LA)
    key = _fl.slot_ledger_key(ROLE, job, slot)
    if _fl.is_scheduler_blocked_by_fallback(continuity_store, ROLE, now=clock):
        return {"status": "skipped", "reason": "fallback_lease_held", "slot_key": key}
    if _fl.should_skip_slot(continuity_store, role=ROLE, job=job, slot=slot):
        return {"status": "skipped", "reason": "slot_already_executed", "slot_key": key}
    _fl.record_slot_claim(continuity_store, role=ROLE, job=job, slot=slot, holder=holder, epoch=epoch, now=clock)
    payload = build_board_secretary_capsule(
        meeting_date=meeting_date,
        seats_reported=seats_reported,
        proposal_dispositions=proposal_dispositions,
        next_slots=next_board_secretary_slots(clock),
    )
    saved = save_board_secretary_capsule(capsule_store, payload, epoch=epoch, now=clock.timestamp())
    msg = {
        "kind": "update",
        "urgency": "normal",
        "summary": summary,
        "body": body,
        "dedupe_key": f"{ROLE}-{job}-{slot}",
    }
    res = _notify.persona_notify(ROLE, msg, **notify_opts, clock=clock.timestamp)
    return {"status": "executed", "slot_key": key, "capsule": saved, "notify": res}


def run_board_secretary_fallback_drill(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    health_store: RoleHealthStore,
    *,
    slot: str,
    meeting_date: str,
    notify_opts: Mapping[str, Any],
    base_time: datetime | None = None,
) -> dict[str, Any]:
    """Run the staging fallback drill: hub down -> Grok takeover -> weekly meeting -> resume -> RD skip."""
    t0 = (base_time or datetime(2026, 11, 6, 19, 50, tzinfo=LA)).timestamp()
    dt = lambda offset: datetime.fromtimestamp(t0 + offset, tz=LA)  # noqa: E731

    def alert(key: str, summary: str, offset: float) -> dict[str, Any]:
        env = {"kind": "alert", "urgency": "high", "summary": summary, "body": summary, "dedupe_key": key}
        return _notify.persona_notify(
            ROLE, {**env, "recommended_action": "Monitor RD hub."}, **notify_opts, clock=lambda: t0 + offset
        )

    health_store.record_heartbeat(ROLE, epoch=1, now=t0)
    health_store.record_hub_probe(ROLE, ok=False, now=t0 + 600.0)
    unhealthy = health_store.record_hub_probe(ROLE, ok=False, now=t0 + 1200.0)
    init_cap = build_board_secretary_capsule(
        meeting_date=meeting_date,
        seats_reported=[],
        proposal_dispositions=[],
        next_slots=next_board_secretary_slots(dt(0)),
    )
    save_board_secretary_capsule(capsule_store, init_cap, epoch=1, now=t0)
    lease_res = _fl.acquire_persona_lease(continuity_store, ROLE, holder=_fl.GROK_FALLBACK_HOLDER, now=dt(1200.0))
    fb_epoch = int((lease_res.get("lease") or lease_res)["epoch"])
    msg = "Board Secretary took over on Grok Bot"
    takeovers = [alert(f"{ROLE}-takeover-{fb_epoch}", msg, o) for o in (1200.0, 1210.0)]
    _fl.record_slot_claim(
        continuity_store,
        role=ROLE,
        job="weekly_board_meeting",
        slot=slot,
        holder=_fl.GROK_FALLBACK_HOLDER,
        epoch=fb_epoch,
        now=dt(1200.0),
    )
    fb_dispositions = [{"proposal_id": "board:proposal-fb", "disposition": "needs-info", "rationale": "hub down"}]
    fb_cap = build_board_secretary_capsule(
        meeting_date=meeting_date,
        seats_reported=["alpha"],
        proposal_dispositions=fb_dispositions,
        next_slots=next_board_secretary_slots(dt(1200.0)),
    )
    fb_slots = [f"{k}:{v}" for k, v in sorted(fb_cap["next_slots"].items())]
    capsule_store.ingest_fallback_capsule(
        ROLE,
        {
            "role": ROLE,
            "epoch": fb_epoch + 1,
            "last_scorecard_date": meeting_date,
            "open_items": fb_cap["open_items"],
            "cursors": fb_cap["cursors"],
            "next_slots": fb_slots,
        },
        now=t0 + 1500.0,
    )
    health_store.record_heartbeat(ROLE, epoch=fb_epoch + 1, now=t0 + 1800.0)
    resumed = health_store.record_heartbeat(ROLE, epoch=fb_epoch + 1, now=t0 + 2400.0)
    _fl.release_persona_lease(continuity_store, ROLE, holder=_fl.GROK_FALLBACK_HOLDER, epoch=fb_epoch, now=dt(2400.0))
    backs = [alert(f"{ROLE}-back-{fb_epoch + 1}", "Board Secretary is back on RD", o) for o in (2400.0, 2410.0)]
    rd_attempt = run_board_secretary_slot(
        continuity_store,
        capsule_store,
        job="weekly_board_meeting",
        slot=slot,
        meeting_date=meeting_date,
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
