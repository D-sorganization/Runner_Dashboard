"""Mad Scientist / Professor Frink RD persona role runtime, capsule schema, shadow-mode
execution, and Grok Bot fallback drill (#3704, next slice of the epic #3695 cutover-tracking
checklist).

Like Research Scout, Pragmatic Programmer and Cartographer (#3704 earlier slices), Mad Scientist
already has a live ``staff/roles/mad-scientist.yml`` in Repository_Management, so this module
ports its real fields rather than the fleet-standard 4-provider chain used by the invented
personas. Its real permissions (``push_branch``/``open_pr`` true, ``lease`` false) match its own
instructions -- it builds draft lab-experiment PRs itself but never leases issues -- so, unlike
Pragmatic Programmer, there is no permission/instruction conflict to flag or override here.
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

ROLE = "mad-scientist"

MAD_SCIENTIST_SCHEDULE_ENTRIES: Mapping[str, str] = {
    "weekly_experiment": "0 22 * * 2",
}

# Real staff/roles/mad-scientist.yml lists 3 providers and "model: default" (no per-step
# override) -- preserved as-is rather than the 4-step chain with model overrides used by the
# invented personas, which do not have a prior real role file to match.
MAD_SCIENTIST_FAILOVER_CHAIN: tuple[FailoverStep, ...] = (
    FailoverStep("claude", None),
    FailoverStep("codex", None),
    FailoverStep("antigravity", None),
)


def mad_scientist_role_data() -> dict[str, Any]:
    """Canonical staff/roles/mad-scientist.yml mapping conforming to staff/schema.json (#3704)."""
    perms = dict.fromkeys(("lease", "host_shell", "notify_user", "merge"), False)
    perms["push_branch"] = True
    perms["open_pr"] = True
    chain = [{"provider": s.provider, **({"model": s.model} if s.model else {})} for s in MAD_SCIENTIST_FAILOVER_CHAIN]
    return {
        "name": ROLE,
        "title": "Mad Scientist (Dr. Frankenstein)",
        "summary": (
            "Builds cheap, deliberately ambitious prototypes for the Runner Dashboard by grafting "
            "ideas from other fields onto it, to find out whether we can; works only in the lab and "
            "never ships."
        ),
        "playbook": "docs/fleet-mad-scientist.md",
        "prompt_template": None,
        "instructions": (
            "You are Mad Scientist, the fleet's weekly 'can we?' prototyper. Builds one cheap "
            "cross-field experiment per run on a lab/<slug> branch, opened as a draft PR labelled "
            "lab-experiment and do-not-merge. Never merges, never marks a lab PR ready, never "
            "deploys, never files issues or Board proposals. Only the owner promotes an experiment "
            "(lab:promote). Schedule: 0 22 * * 2 (weekly_experiment, Tuesday 10 PM) in "
            "America/Los_Angeles."
        ),
        "providers": ["claude", "codex", "antigravity"],
        "model": "default",
        "schedule": "0 22 * * 2",
        "window": None,
        "repos": ["Runner_Dashboard", "Repository_Management"],
        "scope": {
            "schedules": dict(MAD_SCIENTIST_SCHEDULE_ENTRIES),
            "timezone": DEFAULT_TZ,
            "branch_prefix": "lab/",
            "pr_draft": True,
            "max_open_experiments": 3,
            "does_not": ["merge", "deploy", "file-issues", "submit-proposals", "lease"],
        },
        "budget": {"usd_per_run": 5.0, "usd_per_day": 5.0},
        "permissions": perms,
        "retry": {"max_attempts": 3, "failover_chain": chain},
        "reports_to": "user",
        "holds": [],
        "surface": "dashboard",
        "persona": {
            "voice": "Dr. Frankenstein: brilliant, restless, gleefully ambitious. Asks 'can we?' not 'should we?'.",
            "greeting": "It's alive! Or it will be. Bring me a wild idea, or ask what is on the bench.",
            "answers": [
                "what experiments are on the lab bench and how they turned out",
                "wild, cross-field ideas for the Runner Dashboard",
                "what it would take to turn a lab prototype into a real feature",
            ],
            "defers_to": ["barb", "vision-quest", "project-steward"],
        },
        "chat": {
            "contract": "staff/prompts/_chat_contract.md",
            "providers": ["claude", "codex", "antigravity"],
            "tools": ["read_briefing", "read_repo", "read_issue", "search_code"],
        },
    }


def write_mad_scientist_role_yaml(directory: Path) -> Path:
    """Write ``mad-scientist.yml`` into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "mad-scientist.yml"
    path.write_text(yaml.safe_dump(mad_scientist_role_data(), sort_keys=False), encoding="utf-8")
    return path


def load_mad_scientist_role_spec(directory: Path, *, raw: bool = False) -> Any:
    """Parse ``mad-scientist.yml`` from ``directory`` into a ``RoleSpec`` (or raw dict when ``raw=True``)."""
    path = directory / "mad-scientist.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if raw else read_and_parse_role_file(path)


def next_mad_scientist_slots(after: datetime, spec: RoleSpec | None = None) -> dict[str, str]:
    """Compute the next fire timestamp in America/Los_Angeles for each Mad Scientist schedule entry."""
    schedules = (
        dict(spec.scope.get("schedules") or MAD_SCIENTIST_SCHEDULE_ENTRIES)
        if spec is not None and isinstance(spec.scope, dict)
        else dict(MAD_SCIENTIST_SCHEDULE_ENTRIES)
    )
    return {job: next_fire(parse_cron(expr), after, tz=DEFAULT_TZ).isoformat() for job, expr in schedules.items()}


def build_mad_scientist_capsule(
    *,
    last_experiment_date: str,
    open_experiments: Sequence[Mapping[str, Any]],
    next_slots: Mapping[str, str],
    cursors: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Mad Scientist capsule payload with experiment date, open lab items, and next slots."""
    items = [
        {
            "id": str(e.get("id") or f"experiment-{i + 1}").strip(),
            "summary": str(e.get("summary") or "").strip(),
            "link": str(e.get("link") or "").strip(),
        }
        for i, e in enumerate(open_experiments)
    ]
    date_s = str(last_experiment_date).strip()
    return {
        "last_scorecard_date": date_s,
        "open_experiments": items,
        "open_items": [{"id": e["id"], "title": e["summary"], "url": e["link"]} for e in items],
        "cursors": dict(cursors or {"last_scorecard_date": date_s}),
        "next_slots": dict(next_slots),
    }


def validate_mad_scientist_capsule(payload: Mapping[str, Any]) -> list[str]:
    """Return contract problems for a Mad Scientist capsule payload (empty list when valid)."""
    problems: list[str] = []
    if not _DATE_RE.match(str(payload.get("last_scorecard_date") or "").strip()):
        problems.append("last_scorecard_date must be YYYY-MM-DD")
    experiments = payload.get("open_experiments")
    if not isinstance(experiments, list):
        problems.append("open_experiments must be a list")
    else:
        for i, item in enumerate(experiments):
            if not isinstance(item, Mapping) or not str(item.get("summary") or "").strip():
                problems.append(f"open_experiments[{i}].summary must be non-empty")
                continue
            if not str(item.get("link") or "").strip().startswith(("https://", "http://")):
                problems.append(f"open_experiments[{i}].link must be a URL")
    slots = payload.get("next_slots")
    if not isinstance(slots, Mapping) or not set(MAD_SCIENTIST_SCHEDULE_ENTRIES).issubset(set(slots)):
        problems.append(f"next_slots must include {sorted(MAD_SCIENTIST_SCHEDULE_ENTRIES)}")
    return problems


def save_mad_scientist_capsule(
    store: RoleCapsuleStore, payload: Mapping[str, Any], *, epoch: int = 1, now: float | None = None
) -> dict[str, Any]:
    """Validate Mad Scientist's capsule schema and persist + mirror via RoleCapsuleStore."""
    if problems := validate_mad_scientist_capsule(payload):
        raise ValueError(f"Invalid Mad Scientist capsule: {'; '.join(problems)}")
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


def run_mad_scientist_slot(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    *,
    job: str,
    slot: str,
    experiment_date: str,
    notify_opts: Mapping[str, Any],
    summary: str = "Mad Scientist weekly experiment",
    body: str = "",
    open_experiments: Sequence[Mapping[str, Any]] = (),
    holder: str = "rd:desk",
    epoch: int = 1,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute one scheduled Mad Scientist slot unless fallback lease is active or already run.

    The shadow-mode thread post below is the #3295/#3296 shadow-comparison telemetry channel
    (``RD_<ROLE>_THREAD_ID`` / ``status: shadow``), distinct from ``permissions.notify_user``
    (false for this role) which only gates direct user-facing chat notifications. This shadow slot
    never pushes a branch or opens a draft lab PR; it only records the capsule and posts the
    shadow-comparison summary, consistent with every other shadow-mode persona in this epic.
    """
    clock = now or datetime.now(LA)
    key = _fl.slot_ledger_key(ROLE, job, slot)
    if _fl.is_scheduler_blocked_by_fallback(continuity_store, ROLE, now=clock):
        return {"status": "skipped", "reason": "fallback_lease_held", "slot_key": key}
    if _fl.should_skip_slot(continuity_store, role=ROLE, job=job, slot=slot):
        return {"status": "skipped", "reason": "slot_already_executed", "slot_key": key}
    _fl.record_slot_claim(continuity_store, role=ROLE, job=job, slot=slot, holder=holder, epoch=epoch, now=clock)
    payload = build_mad_scientist_capsule(
        last_experiment_date=experiment_date,
        open_experiments=open_experiments,
        next_slots=next_mad_scientist_slots(clock),
    )
    saved = save_mad_scientist_capsule(capsule_store, payload, epoch=epoch, now=clock.timestamp())
    msg = {
        "kind": "update",
        "urgency": "normal",
        "summary": summary,
        "body": body,
        "dedupe_key": f"{ROLE}-{job}-{slot}",
    }
    res = _notify.persona_notify(ROLE, msg, **notify_opts, clock=clock.timestamp)
    return {"status": "executed", "slot_key": key, "capsule": saved, "notify": res}


def run_mad_scientist_fallback_drill(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    health_store: RoleHealthStore,
    *,
    slot: str,
    experiment_date: str,
    notify_opts: Mapping[str, Any],
    base_time: datetime | None = None,
) -> dict[str, Any]:
    """Run the staging fallback drill: hub down -> Grok takeover -> weekly experiment -> resume -> RD skip."""
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
    init_cap = build_mad_scientist_capsule(
        last_experiment_date=experiment_date, open_experiments=[], next_slots=next_mad_scientist_slots(dt(0))
    )
    save_mad_scientist_capsule(capsule_store, init_cap, epoch=1, now=t0)
    lease_res = _fl.acquire_persona_lease(continuity_store, ROLE, holder=_fl.GROK_FALLBACK_HOLDER, now=dt(1200.0))
    fb_epoch = int((lease_res.get("lease") or lease_res)["epoch"])
    t_1 = alert(f"{ROLE}-takeover-{fb_epoch}", "Mad Scientist took over on Grok Bot (hub unreachable)", 1200.0)
    t_2 = alert(f"{ROLE}-takeover-{fb_epoch}", "Mad Scientist took over on Grok Bot (hub unreachable)", 1210.0)
    _fl.record_slot_claim(
        continuity_store,
        role=ROLE,
        job="weekly_experiment",
        slot=slot,
        holder=_fl.GROK_FALLBACK_HOLDER,
        epoch=fb_epoch,
        now=dt(1200.0),
    )
    fb_experiments = [{"id": "experiment-fb", "summary": "Verify RD hub", "link": "https://example.com/3704"}]
    fb_cap = build_mad_scientist_capsule(
        last_experiment_date=experiment_date,
        open_experiments=fb_experiments,
        next_slots=next_mad_scientist_slots(dt(1200.0)),
    )
    fb_slots = [f"{k}:{v}" for k, v in sorted(fb_cap["next_slots"].items())]
    capsule_store.ingest_fallback_capsule(
        ROLE,
        {
            "role": ROLE,
            "epoch": fb_epoch + 1,
            "last_scorecard_date": experiment_date,
            "open_items": fb_cap["open_items"],
            "cursors": fb_cap["cursors"],
            "next_slots": fb_slots,
        },
        now=t0 + 1500.0,
    )
    health_store.record_heartbeat(ROLE, epoch=fb_epoch + 1, now=t0 + 1800.0)
    resumed = health_store.record_heartbeat(ROLE, epoch=fb_epoch + 1, now=t0 + 2400.0)
    _fl.release_persona_lease(continuity_store, ROLE, holder=_fl.GROK_FALLBACK_HOLDER, epoch=fb_epoch, now=dt(2400.0))
    b_1 = alert(f"{ROLE}-back-{fb_epoch + 1}", "Mad Scientist is back on RD", 2400.0)
    b_2 = alert(f"{ROLE}-back-{fb_epoch + 1}", "Mad Scientist is back on RD", 2410.0)
    rd_attempt = run_mad_scientist_slot(
        continuity_store,
        capsule_store,
        job="weekly_experiment",
        slot=slot,
        experiment_date=experiment_date,
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
