"""Fleet Critic RD persona role runtime, capsule schema, shadow-mode execution, and Grok Bot
fallback drill (#3704, next slice of the epic #3695 cutover-tracking checklist).

Flagged discrepancy (reported to Barb, not corrected here, same treatment as the Pragmatic
Programmer slice's own flagged mismatch): the real RM role file sets
``permissions.lease/push_branch/open_pr`` to ``true``, but the same file's own
``scope.does_not`` lists ``implement``/``file-issues``/``lease``. This port follows the
``permissions`` block as the executable contract (``lease``/``push_branch``/``open_pr`` all
``True``, ``merge``/``host_shell``/``notify_user`` all ``False``), treating ``does_not`` as
Barb's prose framing rather than the enforced grant, matching every prior ported persona.
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

ROLE = "fleet-critic"

FLEET_CRITIC_SCHEDULE_ENTRIES: Mapping[str, str] = {
    "weekly_critic_report": "0 12 * * 5",
}

# Real staff/roles/fleet-critic.yml lists 4 providers and "model: default" (no per-step
# override), matching the fleet-standard chain used by the other ported personas.
FLEET_CRITIC_FAILOVER_CHAIN: tuple[FailoverStep, ...] = (
    FailoverStep("claude", None),
    FailoverStep("codex", None),
    FailoverStep("antigravity", None),
    FailoverStep("cursor-agent", None),
)

FLEET_CRITIC_REPOS: tuple[str, ...] = ("UpstreamDrift", "AffineDrift", "Gasification_Model", "Tools")
FLEET_CRITIC_SEVERITIES: tuple[str, ...] = ("critical", "major", "minor", "info")


def fleet_critic_role_data() -> dict[str, Any]:
    """Canonical staff/roles/fleet-critic.yml mapping conforming to staff/schema.json (#3704)."""
    perms = dict.fromkeys(("merge", "host_shell", "notify_user"), False)
    perms["lease"] = True
    perms["push_branch"] = True
    perms["open_pr"] = True
    chain = [{"provider": s.provider, **({"model": s.model} if s.model else {})} for s in FLEET_CRITIC_FAILOVER_CHAIN]
    return {
        "name": ROLE,
        "title": "Fleet Critic",
        "summary": (
            "Adversarial scientific reviewer for the science repos; critic reports for the Friday Board packet."
        ),
        "playbook": "docs/fleet-critic.md",
        "prompt_template": None,
        "instructions": (
            "Adversarial scientific reviewer for UpstreamDrift, AffineDrift, Gasification_Model, and Tools. "
            "Pokes holes in scientific validity, unstated assumptions, overclaiming, and weak evidence; "
            "suggests stronger tests and literature. Reuses the AffineDrift Critic template spirit. Does "
            "not implement code or steal leases. Writes critic reports under the Board/critic archive paths "
            "in Repository_Management. Scheduled weekly ahead of the Friday Board Meeting; also on-demand "
            "when Barb assigns a repo. Interim: reports to barb until the first Board meeting is recorded "
            "in docs/board-meetings/pathway-log.md (RM#1766 Phase 0); then revert to board-secretary."
        ),
        "providers": ["claude", "codex", "antigravity", "cursor-agent"],
        "model": "default",
        "schedule": "0 12 * * 5",
        "window": None,
        "repos": list(FLEET_CRITIC_REPOS),
        "scope": {
            "schedules": dict(FLEET_CRITIC_SCHEDULE_ENTRIES),
            "rotation": "per docs/fleet-critic.md schedule table",
            "archive": "docs/assessments/board",
            "does_not": ["implement", "file-issues", "lease"],
        },
        "budget": {"usd_per_run": 4.0, "usd_per_day": 4.0},
        "permissions": perms,
        "retry": {"max_attempts": 4, "failover_chain": chain},
        "reports_to": "barb",
        "holds": [],
        "surface": "both",
        "persona": {
            "voice": "Adversarial reviewer. States the weakest claim first, names the evidence that would settle it.",
            "greeting": "Fleet Critic. Name a claim, a model or a result and I will tell you what would falsify it.",
            "answers": [
                "whether a scientific claim is supported by what is in the repository",
                "what evidence a result is missing",
                "which assumptions a model quietly rests on",
                "what the last critic report found and what changed since",
            ],
            "defers_to": ["librarian", "research-scout", "board-secretary"],
        },
        "chat": {
            "contract": "staff/prompts/_chat_contract.md",
            "providers": ["claude", "codex", "antigravity", "cursor-agent"],
            "tools": ["read_repo", "search_code", "read_issue"],
        },
    }


def write_fleet_critic_role_yaml(directory: Path) -> Path:
    """Write ``fleet-critic.yml`` into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "fleet-critic.yml"
    path.write_text(yaml.safe_dump(fleet_critic_role_data(), sort_keys=False), encoding="utf-8")
    return path


def load_fleet_critic_role_spec(directory: Path, *, raw: bool = False) -> Any:
    """Parse ``fleet-critic.yml`` from ``directory`` into a ``RoleSpec`` (or raw dict when ``raw=True``)."""
    path = directory / "fleet-critic.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if raw else read_and_parse_role_file(path)


def next_fleet_critic_slots(after: datetime, spec: RoleSpec | None = None) -> dict[str, str]:
    """Compute the next fire timestamp in America/Los_Angeles for each Fleet Critic schedule entry."""
    schedules = (
        dict(spec.scope.get("schedules") or FLEET_CRITIC_SCHEDULE_ENTRIES)
        if spec is not None and isinstance(spec.scope, dict)
        else dict(FLEET_CRITIC_SCHEDULE_ENTRIES)
    )
    return {job: next_fire(parse_cron(expr), after, tz=DEFAULT_TZ).isoformat() for job, expr in schedules.items()}


def build_fleet_critic_capsule(
    *,
    report_date: str,
    critic_reports: Sequence[Mapping[str, Any]],
    next_slots: Mapping[str, str],
    cursors: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Fleet Critic capsule payload with the report date, per-repo findings, and next slots."""
    keys = ("repo", "severity", "summary", "link")
    reports = [{k: str(r.get(k) or "").strip() for k in keys} for r in critic_reports]
    date_s = str(report_date).strip()
    return {
        "last_scorecard_date": date_s,
        "critic_reports": reports,
        "open_items": [
            {"id": f"{r['repo']}:{i}", "title": f"{r['severity']}: {r['summary']}", "url": r["link"]}
            for i, r in enumerate(reports)
            if r["severity"] in ("critical", "major")
        ],
        "cursors": dict(cursors or {"last_scorecard_date": date_s}),
        "next_slots": dict(next_slots),
    }


def validate_fleet_critic_capsule(payload: Mapping[str, Any]) -> list[str]:
    """Return contract problems for a Fleet Critic capsule payload (empty list when valid)."""
    problems: list[str] = []
    if not _DATE_RE.match(str(payload.get("last_scorecard_date") or "").strip()):
        problems.append("last_scorecard_date must be YYYY-MM-DD")
    reports = payload.get("critic_reports")
    if not isinstance(reports, list):
        problems.append("critic_reports must be a list")
    else:
        for i, r in enumerate(reports):
            if not isinstance(r, Mapping) or str(r.get("repo") or "").strip() not in FLEET_CRITIC_REPOS:
                problems.append(f"critic_reports[{i}].repo must be one of {list(FLEET_CRITIC_REPOS)}")
                continue
            if str(r.get("severity") or "").strip() not in FLEET_CRITIC_SEVERITIES:
                problems.append(f"critic_reports[{i}].severity must be one of {list(FLEET_CRITIC_SEVERITIES)}")
            if not str(r.get("summary") or "").strip():
                problems.append(f"critic_reports[{i}].summary must be non-empty")
    slots = payload.get("next_slots")
    if not isinstance(slots, Mapping) or not set(FLEET_CRITIC_SCHEDULE_ENTRIES).issubset(set(slots)):
        problems.append(f"next_slots must include {sorted(FLEET_CRITIC_SCHEDULE_ENTRIES)}")
    return problems


def save_fleet_critic_capsule(
    store: RoleCapsuleStore, payload: Mapping[str, Any], *, epoch: int = 1, now: float | None = None
) -> dict[str, Any]:
    """Validate Fleet Critic's capsule schema and persist + mirror via RoleCapsuleStore."""
    if problems := validate_fleet_critic_capsule(payload):
        raise ValueError(f"Invalid Fleet Critic capsule: {'; '.join(problems)}")
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


def run_fleet_critic_slot(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    *,
    job: str,
    slot: str,
    report_date: str,
    notify_opts: Mapping[str, Any],
    summary: str = "Fleet Critic weekly report",
    body: str = "",
    critic_reports: Sequence[Mapping[str, Any]] = (),
    holder: str = "rd:desk",
    epoch: int = 1,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute one scheduled Fleet Critic slot unless fallback lease is active or already run.

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
    payload = build_fleet_critic_capsule(
        report_date=report_date, critic_reports=critic_reports, next_slots=next_fleet_critic_slots(clock)
    )
    saved = save_fleet_critic_capsule(capsule_store, payload, epoch=epoch, now=clock.timestamp())
    msg = {
        "kind": "update",
        "urgency": "normal",
        "summary": summary,
        "body": body,
        "dedupe_key": f"{ROLE}-{job}-{slot}",
    }
    res = _notify.persona_notify(ROLE, msg, **notify_opts, clock=clock.timestamp)
    return {"status": "executed", "slot_key": key, "capsule": saved, "notify": res}


def run_fleet_critic_fallback_drill(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    health_store: RoleHealthStore,
    *,
    slot: str,
    report_date: str,
    notify_opts: Mapping[str, Any],
    base_time: datetime | None = None,
) -> dict[str, Any]:
    """Run the staging fallback drill: hub down -> Grok takeover -> weekly report -> resume -> RD skip."""
    t0 = (base_time or datetime(2026, 11, 6, 11, 50, tzinfo=LA)).timestamp()
    dt = lambda offset: datetime.fromtimestamp(t0 + offset, tz=LA)  # noqa: E731

    def alert(key: str, summary: str, offset: float) -> dict[str, Any]:
        env = {"kind": "alert", "urgency": "high", "summary": summary, "body": summary, "dedupe_key": key}
        return _notify.persona_notify(
            ROLE, {**env, "recommended_action": "Monitor RD hub."}, **notify_opts, clock=lambda: t0 + offset
        )

    health_store.record_heartbeat(ROLE, epoch=1, now=t0)
    health_store.record_hub_probe(ROLE, ok=False, now=t0 + 600.0)
    unhealthy = health_store.record_hub_probe(ROLE, ok=False, now=t0 + 1200.0)
    init_cap = build_fleet_critic_capsule(
        report_date=report_date, critic_reports=[], next_slots=next_fleet_critic_slots(dt(0))
    )
    save_fleet_critic_capsule(capsule_store, init_cap, epoch=1, now=t0)
    lease_res = _fl.acquire_persona_lease(continuity_store, ROLE, holder=_fl.GROK_FALLBACK_HOLDER, now=dt(1200.0))
    fb_epoch = int((lease_res.get("lease") or lease_res)["epoch"])
    msg = "Fleet Critic took over on Grok Bot"
    takeovers = [alert(f"{ROLE}-takeover-{fb_epoch}", msg, o) for o in (1200.0, 1210.0)]
    _fl.record_slot_claim(
        continuity_store,
        role=ROLE,
        job="weekly_critic_report",
        slot=slot,
        holder=_fl.GROK_FALLBACK_HOLDER,
        epoch=fb_epoch,
        now=dt(1200.0),
    )
    fb_reports = [
        {"repo": "UpstreamDrift", "severity": "major", "summary": "hub down", "link": "https://example.com/3704"}
    ]
    fb_cap = build_fleet_critic_capsule(
        report_date=report_date, critic_reports=fb_reports, next_slots=next_fleet_critic_slots(dt(1200.0))
    )
    fb_slots = [f"{k}:{v}" for k, v in sorted(fb_cap["next_slots"].items())]
    capsule_store.ingest_fallback_capsule(
        ROLE,
        {
            "role": ROLE,
            "epoch": fb_epoch + 1,
            "last_scorecard_date": report_date,
            "open_items": fb_cap["open_items"],
            "cursors": fb_cap["cursors"],
            "next_slots": fb_slots,
        },
        now=t0 + 1500.0,
    )
    health_store.record_heartbeat(ROLE, epoch=fb_epoch + 1, now=t0 + 1800.0)
    resumed = health_store.record_heartbeat(ROLE, epoch=fb_epoch + 1, now=t0 + 2400.0)
    _fl.release_persona_lease(continuity_store, ROLE, holder=_fl.GROK_FALLBACK_HOLDER, epoch=fb_epoch, now=dt(2400.0))
    backs = [alert(f"{ROLE}-back-{fb_epoch + 1}", "Fleet Critic is back on RD", o) for o in (2400.0, 2410.0)]
    rd_attempt = run_fleet_critic_slot(
        continuity_store,
        capsule_store,
        job="weekly_critic_report",
        slot=slot,
        report_date=report_date,
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
