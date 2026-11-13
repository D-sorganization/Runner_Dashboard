"""Sanitation Engineer RD persona role runtime, capsule schema, shadow-mode execution, and Grok
Bot fallback drill (#3704). Ports the real ``staff/roles/sanitation.yml`` verbatim (no ``retry`` block)."""

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
from staff.provider_failover_config import FailoverStep
from staff.role_spec import RoleSpec, read_and_parse_role_file  # noqa: E402
from staff.schedule import DEFAULT_TZ, next_fire, parse_cron  # noqa: E402

from scripts import rd_barb_notify as _notify  # noqa: E402

LA = ZoneInfo(DEFAULT_TZ)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

ROLE = "sanitation"

SANITATION_FAILOVER_CHAIN: tuple[FailoverStep, ...] = (
    FailoverStep("codex", "gpt-6.1-sol"),
    FailoverStep("claude", "claude-opus-5-5"),
    FailoverStep("cursor-agent", None),
)

SANITATION_SCHEDULE_ENTRIES: Mapping[str, str] = {
    "overnight_sweep": "0 3 * * *",
}

SANITATION_TARGETS: tuple[str, ...] = (
    "stale-branches",
    "stale-prs",
    "scratch-worktrees",
    "temp-artifacts",
    "docs-ia",
)
SANITATION_NEVER_DELETE: tuple[str, ...] = ("dirty-worktree", "open-pr-branch", "canonical")
SANITATION_REPOS: tuple[str, ...] = (
    "UpstreamDrift",
    "AffineDrift",
    "Gasification_Model",
    "Tools",
    "Tools_Private",
    "Repository_Management",
    "Runner_Dashboard",
)


def sanitation_role_data() -> dict[str, Any]:
    """Canonical staff/roles/sanitation.yml mapping conforming to staff/schema.json (#3704)."""
    perms = dict.fromkeys(("merge", "host_shell", "notify_user"), False)
    perms["lease"] = True
    perms["push_branch"] = True
    perms["open_pr"] = True
    return {
        "name": ROLE,
        "title": "Sanitation Engineer",
        "summary": (
            "Fleet anti-clutter maintenance: stale branches/PRs, scratch worktrees, temp junk, "
            "docs IA hygiene (fail-closed)."
        ),
        "playbook": "docs/fleet-sanitation.md",
        "prompt_template": "docs/templates/sanitation-report.md",
        "instructions": (
            "Fleet cleanup and anti-clutter maintenance across D-sorganization repos: stale branches/PRs "
            "(fail-closed), scratch worktrees, temp/generated junk, docs IA hygiene, leftover session debris.\n\n"
            "Safety-first: never delete dirty, open-PR, or canonical work. Source of truth is the "
            "Repository_Management playbook, not bot memory. Coordinates with Night Watch (overnight triage) "
            "and Cartographer (maps). Does not steal claims, no admin merges, no Board remediation floods."
        ),
        "providers": ["codex", "claude", "cursor-agent"],
        "model": "default",
        "schedule": "0 3 * * *",
        "window": {"start": "22:00", "end": "06:00"},
        "repos": list(SANITATION_REPOS),
        "scope": {
            "schedules": dict(SANITATION_SCHEDULE_ENTRIES),
            "timezone": DEFAULT_TZ,
            "targets": list(SANITATION_TARGETS),
            "never_delete": list(SANITATION_NEVER_DELETE),
            "mode": "fail-closed",
            "max_lease_hours": 2,
        },
        "budget": {"usd_per_run": 4.0, "usd_per_day": 4.0},
        "permissions": perms,
        "reports_to": "night-watch",
        "holds": [],
        "surface": "dashboard",
        "persona": {
            "voice": "Fail-closed janitor. Names what it refuses to touch before it names what it would remove.",
            "greeting": "Sanitation. Ask me what is safe to remove - I will tell you what I refuse to touch first.",
            "answers": [
                "whether a branch, worktree or artifact is safe to remove",
                "why something was kept",
                "how much clutter a repository is carrying",
                "what the last sweep removed",
            ],
            "defers_to": ["night-watch", "cartographer"],
        },
        "chat": {
            "contract": "staff/prompts/_chat_contract.md",
            "providers": ["claude", "codex", "antigravity"],
            "tools": ["read_briefing", "read_run", "read_repo", "search_code"],
        },
    }


def write_sanitation_role_yaml(directory: Path) -> Path:
    """Write ``sanitation.yml`` into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "sanitation.yml"
    path.write_text(yaml.safe_dump(sanitation_role_data(), sort_keys=False), encoding="utf-8")
    return path


def load_sanitation_role_spec(directory: Path, *, raw: bool = False) -> Any:
    """Parse ``sanitation.yml`` from ``directory`` into a ``RoleSpec`` (or raw dict when ``raw=True``)."""
    path = directory / "sanitation.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if raw else read_and_parse_role_file(path)


def next_sanitation_slots(after: datetime, spec: RoleSpec | None = None) -> dict[str, str]:
    """Compute the next fire timestamp in America/Los_Angeles for each Sanitation schedule entry."""
    schedules = (
        dict(spec.scope.get("schedules") or SANITATION_SCHEDULE_ENTRIES)
        if spec is not None and isinstance(spec.scope, dict)
        else dict(SANITATION_SCHEDULE_ENTRIES)
    )
    return {job: next_fire(parse_cron(expr), after, tz=DEFAULT_TZ).isoformat() for job, expr in schedules.items()}


def build_sanitation_capsule(
    *,
    last_sweep_date: str,
    swept_items: Sequence[Mapping[str, Any]],
    next_slots: Mapping[str, str],
    cursors: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Sanitation capsule payload with the sweep date, swept items, and next slots."""
    items = [
        {
            "id": str(e.get("id") or f"sweep-{i + 1}").strip(),
            "decision": str(e.get("decision") or "kept").strip(),
            "summary": str(e.get("summary") or "").strip(),
            "link": str(e.get("link") or "").strip(),
        }
        for i, e in enumerate(swept_items)
    ]
    date_s = str(last_sweep_date).strip()
    return {
        "last_scorecard_date": date_s,
        "swept_items": items,
        "open_items": [{"id": e["id"], "title": e["summary"], "url": e["link"]} for e in items],
        "cursors": dict(cursors or {"last_scorecard_date": date_s}),
        "next_slots": dict(next_slots),
    }


def validate_sanitation_capsule(payload: Mapping[str, Any]) -> list[str]:
    """Return contract problems for a Sanitation capsule payload (empty list when valid)."""
    problems: list[str] = []
    if not _DATE_RE.match(str(payload.get("last_scorecard_date") or "").strip()):
        problems.append("last_scorecard_date must be YYYY-MM-DD")
    items = payload.get("swept_items")
    if not isinstance(items, list):
        problems.append("swept_items must be a list")
    else:
        decisions = ("removed", "kept", "skipped")
        for i, item in enumerate(items):
            if not isinstance(item, Mapping) or not str(item.get("summary") or "").strip():
                problems.append(f"swept_items[{i}].summary must be non-empty")
                continue
            if item.get("decision") not in decisions:
                problems.append(f"swept_items[{i}].decision must be one of {decisions}")
            if not str(item.get("link") or "").strip().startswith(("https://", "http://")):
                problems.append(f"swept_items[{i}].link must be a URL")
    slots = payload.get("next_slots")
    if not isinstance(slots, Mapping) or not set(SANITATION_SCHEDULE_ENTRIES).issubset(set(slots)):
        problems.append(f"next_slots must include {sorted(SANITATION_SCHEDULE_ENTRIES)}")
    return problems


def save_sanitation_capsule(
    store: RoleCapsuleStore, payload: Mapping[str, Any], *, epoch: int = 1, now: float | None = None
) -> dict[str, Any]:
    """Validate Sanitation's capsule schema and persist + mirror via RoleCapsuleStore."""
    if problems := validate_sanitation_capsule(payload):
        raise ValueError(f"Invalid Sanitation capsule: {'; '.join(problems)}")
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


def run_sanitation_slot(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    *,
    job: str,
    slot: str,
    sweep_date: str,
    notify_opts: Mapping[str, Any],
    summary: str = "Sanitation overnight sweep",
    body: str = "",
    swept_items: Sequence[Mapping[str, Any]] = (),
    holder: str = "rd:desk",
    epoch: int = 1,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute one scheduled Sanitation slot unless fallback lease is active or already run."""
    clock = now or datetime.now(LA)
    key = _fl.slot_ledger_key(ROLE, job, slot)
    if _fl.is_scheduler_blocked_by_fallback(continuity_store, ROLE, now=clock):
        return {"status": "skipped", "reason": "fallback_lease_held", "slot_key": key}
    if _fl.should_skip_slot(continuity_store, role=ROLE, job=job, slot=slot):
        return {"status": "skipped", "reason": "slot_already_executed", "slot_key": key}
    _fl.record_slot_claim(continuity_store, role=ROLE, job=job, slot=slot, holder=holder, epoch=epoch, now=clock)
    payload = build_sanitation_capsule(
        last_sweep_date=sweep_date,
        swept_items=swept_items,
        next_slots=next_sanitation_slots(clock),
    )
    saved = save_sanitation_capsule(capsule_store, payload, epoch=epoch, now=clock.timestamp())
    msg = {
        "kind": "update",
        "urgency": "normal",
        "summary": summary,
        "body": body,
        "dedupe_key": f"{ROLE}-{job}-{slot}",
    }
    res = _notify.persona_notify(ROLE, msg, **notify_opts, clock=clock.timestamp)
    return {"status": "executed", "slot_key": key, "capsule": saved, "notify": res}


def run_sanitation_fallback_drill(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    health_store: RoleHealthStore,
    *,
    slot: str,
    sweep_date: str,
    notify_opts: Mapping[str, Any],
    base_time: datetime | None = None,
) -> dict[str, Any]:
    """Run the staging fallback drill: hub down -> Grok takeover -> overnight sweep -> resume -> RD skip."""
    t0 = (base_time or datetime(2026, 11, 3, 3, 50, tzinfo=LA)).timestamp()
    dt = lambda offset: datetime.fromtimestamp(t0 + offset, tz=LA)  # noqa: E731

    def alert(key: str, summary: str, offset: float) -> dict[str, Any]:
        env = {"kind": "alert", "urgency": "high", "summary": summary, "body": summary, "dedupe_key": key}
        return _notify.persona_notify(
            ROLE, {**env, "recommended_action": "Monitor RD hub."}, **notify_opts, clock=lambda: t0 + offset
        )

    health_store.record_heartbeat(ROLE, epoch=1, now=t0)
    health_store.record_hub_probe(ROLE, ok=False, now=t0 + 600.0)
    unhealthy = health_store.record_hub_probe(ROLE, ok=False, now=t0 + 1200.0)
    init_cap = build_sanitation_capsule(
        last_sweep_date=sweep_date, swept_items=[], next_slots=next_sanitation_slots(dt(0))
    )
    save_sanitation_capsule(capsule_store, init_cap, epoch=1, now=t0)
    lease_res = _fl.acquire_persona_lease(continuity_store, ROLE, holder=_fl.GROK_FALLBACK_HOLDER, now=dt(1200.0))
    fb_epoch = int((lease_res.get("lease") or lease_res)["epoch"])
    msg = "Sanitation took over on Grok Bot"
    takeovers = [alert(f"{ROLE}-takeover-{fb_epoch}", msg, o) for o in (1200.0, 1210.0)]
    _fl.record_slot_claim(
        continuity_store,
        role=ROLE,
        job="overnight_sweep",
        slot=slot,
        holder=_fl.GROK_FALLBACK_HOLDER,
        epoch=fb_epoch,
        now=dt(1200.0),
    )
    fb_items = [{"id": "sweep-fb", "decision": "kept", "summary": "Verify RD hub", "link": "https://example.com/3704"}]
    fb_cap = build_sanitation_capsule(
        last_sweep_date=sweep_date, swept_items=fb_items, next_slots=next_sanitation_slots(dt(1200.0))
    )
    fb_slots = [f"{k}:{v}" for k, v in sorted(fb_cap["next_slots"].items())]
    capsule_store.ingest_fallback_capsule(
        ROLE,
        {
            "role": ROLE,
            "epoch": fb_epoch + 1,
            "last_scorecard_date": sweep_date,
            "open_items": fb_cap["open_items"],
            "cursors": fb_cap["cursors"],
            "next_slots": fb_slots,
        },
        now=t0 + 1500.0,
    )
    health_store.record_heartbeat(ROLE, epoch=fb_epoch + 1, now=t0 + 1800.0)
    resumed = health_store.record_heartbeat(ROLE, epoch=fb_epoch + 1, now=t0 + 2400.0)
    _fl.release_persona_lease(continuity_store, ROLE, holder=_fl.GROK_FALLBACK_HOLDER, epoch=fb_epoch, now=dt(2400.0))
    backs = [alert(f"{ROLE}-back-{fb_epoch + 1}", "Sanitation is back on RD", o) for o in (2400.0, 2410.0)]
    rd_attempt = run_sanitation_slot(
        continuity_store,
        capsule_store,
        job="overnight_sweep",
        slot=slot,
        sweep_date=sweep_date,
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
