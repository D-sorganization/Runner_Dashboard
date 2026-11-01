"""Silent Cartographer RD persona role runtime, capsule schema, shadow-mode execution, and
Grok Bot fallback drill (#3704, next slice of the epic #3695 cutover-tracking checklist).

Like Research Scout and Pragmatic Programmer (#3704 slices 1-2), Cartographer already has a
live ``staff/roles/cartographer.yml`` in Repository_Management, so this module ports its real
fields. Unlike those two report-only roles, Cartographer's real role file grants
``lease``/``push_branch``/``open_pr`` (it owns and pushes codemap updates), consistent with its
own instructions -- no permission conflict to flag here. Its real overnight ``window``
(22:00-06:00) is gated generically by ``staff/scheduler.py`` via ``staff.schedule.in_window``.
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

ROLE = "cartographer"

CARTOGRAPHER_SCHEDULE_ENTRIES: Mapping[str, str] = {
    "weekly_map_sweep": "0 4 * * 0",
}
CARTOGRAPHER_WINDOW: Mapping[str, str] = {"start": "22:00", "end": "06:00"}

# Real staff/roles/cartographer.yml lists 3 providers and "model: default" (no per-step model
# override) -- preserved as-is rather than the 4-step chain with model overrides used by the
# invented personas, which do not have a prior real role file to match.
CARTOGRAPHER_FAILOVER_CHAIN: tuple[FailoverStep, ...] = (
    FailoverStep("claude", None),
    FailoverStep("codex", None),
    FailoverStep("antigravity", None),
)

_STALE_REPOS_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def cartographer_role_data() -> dict[str, Any]:
    """Canonical staff/roles/cartographer.yml mapping conforming to staff/schema.json (#3704)."""
    perms = dict.fromkeys(("lease", "push_branch", "open_pr", "merge", "host_shell", "notify_user"), False)
    perms["lease"] = True
    perms["push_branch"] = True
    perms["open_pr"] = True
    chain = [{"provider": s.provider, **({"model": s.model} if s.model else {})} for s in CARTOGRAPHER_FAILOVER_CHAIN]
    return {
        "name": ROLE,
        "title": "The Silent Cartographer",
        "summary": "Keeps codebase maps accurate so any agent can rapidly understand UD, AD, GM, Tools RM and RD.",
        "playbook": "docs/fleet-cartographer.md",
        "prompt_template": "docs/templates/cartographer-report.md",
        "instructions": (
            "Maintains accurate codebase maps so any agent (bot or not) can rapidly understand "
            "UpstreamDrift, AffineDrift, Gasification_Model, Tools, Repository_Management and "
            "Runner_Dashboard. Incorporates existing repo mapping guidelines; suggests "
            "improvements; keeps docs pointing at the maps. Source of truth is in-repo map "
            "artifacts plus RM docs, never bot-only memory. Does not implement product features "
            "or steal leases. Runs with Night Watch; reports map freshness to "
            "Orchestrator/Board."
        ),
        "providers": ["claude", "codex", "antigravity"],
        "model": "default",
        "schedule": "0 4 * * 0",
        "window": dict(CARTOGRAPHER_WINDOW),
        "repos": [
            "UpstreamDrift",
            "AffineDrift",
            "Gasification_Model",
            "Tools",
            "Repository_Management",
            "Runner_Dashboard",
        ],
        "tools": ["search_knowledge"],
        "scope": {
            "schedules": dict(CARTOGRAPHER_SCHEDULE_ENTRIES),
            "timezone": DEFAULT_TZ,
            "pack": "cartographer",
            "access": "read_only",
            "owns": ["codemaps", "map-freshness-report"],
            "artifacts": ["docs/codemap", "docs/architecture"],
        },
        "budget": {"usd_per_run": 6.0, "usd_per_day": 6.0},
        "permissions": perms,
        "retry": {"max_attempts": 4, "failover_chain": chain},
        "reports_to": "barb",
        "holds": [],
        "surface": "dashboard",
        "persona": {
            "voice": "Surveyor's plainness. Says where a thing lives, and how stale the map that says so is.",
            "greeting": (
                "Cartographer. Ask me where something lives in UD, AD, GM, Tools, RM or RD, and how fresh that map is."
            ),
            "answers": [
                "where a module, boundary or entry point lives",
                "how stale a codemap or architecture doc is",
                "which repositories have no current map",
                "what a given map artifact does and does not cover",
            ],
            "defers_to": ["librarian", "night-watch"],
        },
        "chat": {
            "contract": "staff/prompts/_chat_contract.md",
            "providers": ["claude", "codex", "antigravity"],
            "tools": ["read_repo", "search_code", "read_briefing", "search_knowledge"],
        },
    }


def write_cartographer_role_yaml(directory: Path) -> Path:
    """Write ``cartographer.yml`` into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "cartographer.yml"
    path.write_text(yaml.safe_dump(cartographer_role_data(), sort_keys=False), encoding="utf-8")
    return path


def load_cartographer_role_spec(directory: Path, *, raw: bool = False) -> Any:
    """Parse ``cartographer.yml`` from ``directory`` into a ``RoleSpec`` (or raw dict when ``raw=True``)."""
    path = directory / "cartographer.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if raw else read_and_parse_role_file(path)


def next_cartographer_slots(after: datetime, spec: RoleSpec | None = None) -> dict[str, str]:
    """Compute the next fire timestamp in America/Los_Angeles for each Cartographer schedule entry."""
    schedules = (
        dict(spec.scope.get("schedules") or CARTOGRAPHER_SCHEDULE_ENTRIES)
        if spec is not None and isinstance(spec.scope, dict)
        else dict(CARTOGRAPHER_SCHEDULE_ENTRIES)
    )
    return {job: next_fire(parse_cron(expr), after, tz=DEFAULT_TZ).isoformat() for job, expr in schedules.items()}


def build_cartographer_capsule(
    *,
    last_sweep_date: str,
    stale_repos: Sequence[str],
    next_slots: Mapping[str, str],
    cursors: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Cartographer capsule payload with sweep date, stale-repo findings, and next slots."""
    repos = [str(r).strip() for r in stale_repos]
    date_s = str(last_sweep_date).strip()
    return {
        "last_sweep_date": date_s,
        "stale_repos": repos,
        "open_items": [{"id": f"stale-{r}", "title": f"Codemap stale: {r}", "url": ""} for r in repos],
        "cursors": dict(cursors or {"last_sweep_date": date_s}),
        "next_slots": dict(next_slots),
    }


def validate_cartographer_capsule(payload: Mapping[str, Any]) -> list[str]:
    """Return contract problems for a Cartographer capsule payload (empty list when valid)."""
    problems: list[str] = []
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(payload.get("last_sweep_date") or "").strip()):
        problems.append("last_sweep_date must be YYYY-MM-DD")
    stale = payload.get("stale_repos")
    if not isinstance(stale, list):
        problems.append("stale_repos must be a list")
    else:
        for i, repo in enumerate(stale):
            if not isinstance(repo, str) or not _STALE_REPOS_RE.match(repo):
                problems.append(f"stale_repos[{i}] must be a valid repo name")
    slots = payload.get("next_slots")
    if not isinstance(slots, Mapping) or not set(CARTOGRAPHER_SCHEDULE_ENTRIES).issubset(set(slots)):
        problems.append(f"next_slots must include {sorted(CARTOGRAPHER_SCHEDULE_ENTRIES)}")
    return problems


def save_cartographer_capsule(
    store: RoleCapsuleStore, payload: Mapping[str, Any], *, epoch: int = 1, now: float | None = None
) -> dict[str, Any]:
    """Validate Cartographer's capsule schema and persist + mirror via RoleCapsuleStore."""
    if problems := validate_cartographer_capsule(payload):
        raise ValueError(f"Invalid Cartographer capsule: {'; '.join(problems)}")
    slots_map = dict(payload["next_slots"])
    date_s = str(payload["last_sweep_date"])
    return store.save_capsule(
        ROLE,
        epoch=epoch,
        last_scorecard_date=date_s,
        open_items=list(payload.get("open_items") or []),
        cursors={**dict(payload.get("cursors") or {}), "last_sweep_date": date_s},
        next_slots=[f"{job}:{ts}" for job, ts in sorted(slots_map.items())],
        now=now,
    )


def run_cartographer_slot(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    *,
    job: str,
    slot: str,
    sweep_date: str,
    notify_opts: Mapping[str, Any],
    summary: str = "Cartographer weekly map sweep",
    body: str = "",
    stale_repos: Sequence[str] = (),
    holder: str = "rd:desk",
    epoch: int = 1,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute one scheduled Cartographer slot unless fallback lease is active or already run.

    The shadow-mode thread post below is the #3295/#3296 shadow-comparison telemetry channel
    (``RD_<ROLE>_THREAD_ID`` / ``status: shadow``), distinct from ``permissions.notify_user``
    (false for this role) which only gates direct user-facing chat notifications.
    """
    clock = now or datetime.now(LA)
    key = _fl.slot_ledger_key(ROLE, job, slot)
    if _fl.is_scheduler_blocked_by_fallback(continuity_store, ROLE, now=clock):
        return {"status": "skipped", "reason": "fallback_lease_held", "slot_key": key}
    if _fl.should_skip_slot(continuity_store, role=ROLE, job=job, slot=slot):
        return {"status": "skipped", "reason": "slot_already_executed", "slot_key": key}
    _fl.record_slot_claim(continuity_store, role=ROLE, job=job, slot=slot, holder=holder, epoch=epoch, now=clock)
    payload = build_cartographer_capsule(
        last_sweep_date=sweep_date, stale_repos=stale_repos, next_slots=next_cartographer_slots(clock)
    )
    saved = save_cartographer_capsule(capsule_store, payload, epoch=epoch, now=clock.timestamp())
    msg = {
        "kind": "update",
        "urgency": "normal",
        "summary": summary,
        "body": body,
        "dedupe_key": f"{ROLE}-{job}-{slot}",
    }
    res = _notify.persona_notify(ROLE, msg, **notify_opts, clock=clock.timestamp)
    return {"status": "executed", "slot_key": key, "capsule": saved, "notify": res}


def run_cartographer_fallback_drill(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    health_store: RoleHealthStore,
    *,
    slot: str,
    sweep_date: str,
    notify_opts: Mapping[str, Any],
    base_time: datetime | None = None,
) -> dict[str, Any]:
    """Run the staging fallback drill: hub down -> Grok takeover -> weekly sweep -> resume -> RD skip."""
    t0 = (base_time or datetime(2026, 11, 8, 3, 50, tzinfo=LA)).timestamp()
    dt = lambda offset: datetime.fromtimestamp(t0 + offset, tz=LA)  # noqa: E731

    def alert(key: str, summary: str, offset: float) -> dict[str, Any]:
        env = {"kind": "alert", "urgency": "high", "summary": summary, "body": summary, "dedupe_key": key}
        return _notify.persona_notify(
            ROLE, {**env, "recommended_action": "Monitor RD hub."}, **notify_opts, clock=lambda: t0 + offset
        )

    health_store.record_heartbeat(ROLE, epoch=1, now=t0)
    health_store.record_hub_probe(ROLE, ok=False, now=t0 + 600.0)
    unhealthy = health_store.record_hub_probe(ROLE, ok=False, now=t0 + 1200.0)
    init_cap = build_cartographer_capsule(
        last_sweep_date=sweep_date, stale_repos=[], next_slots=next_cartographer_slots(dt(0))
    )
    save_cartographer_capsule(capsule_store, init_cap, epoch=1, now=t0)
    lease_res = _fl.acquire_persona_lease(continuity_store, ROLE, holder=_fl.GROK_FALLBACK_HOLDER, now=dt(1200.0))
    fb_epoch = int((lease_res.get("lease") or lease_res)["epoch"])
    t_1 = alert(f"{ROLE}-takeover-{fb_epoch}", "Cartographer took over on Grok Bot (hub unreachable)", 1200.0)
    t_2 = alert(f"{ROLE}-takeover-{fb_epoch}", "Cartographer took over on Grok Bot (hub unreachable)", 1210.0)
    _fl.record_slot_claim(
        continuity_store,
        role=ROLE,
        job="weekly_map_sweep",
        slot=slot,
        holder=_fl.GROK_FALLBACK_HOLDER,
        epoch=fb_epoch,
        now=dt(1200.0),
    )
    fb_stale = ["Gasification_Model"]
    fb_cap = build_cartographer_capsule(
        last_sweep_date=sweep_date, stale_repos=fb_stale, next_slots=next_cartographer_slots(dt(1200.0))
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
    b_1 = alert(f"{ROLE}-back-{fb_epoch + 1}", "Cartographer is back on RD", 2400.0)
    b_2 = alert(f"{ROLE}-back-{fb_epoch + 1}", "Cartographer is back on RD", 2410.0)
    rd_attempt = run_cartographer_slot(
        continuity_store,
        capsule_store,
        job="weekly_map_sweep",
        slot=slot,
        sweep_date=sweep_date,
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
        "final_capsule": capsule_store.load_capsule(ROLE),
    }
