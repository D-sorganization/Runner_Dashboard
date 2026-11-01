"""Pragmatic Programmer RD persona role runtime, capsule schema, shadow-mode execution, and Grok
Bot fallback drill (#3704, next slice of the epic #3695 cutover-tracking checklist).

Like Research Scout (#3704 slice 1), Pragmatic Programmer already has a live
``staff/roles/pragmatic-programmer.yml`` in Repository_Management, so this module ports its
real fields rather than the fleet-standard 4-provider chain used by the invented personas.

Flagged discrepancy (reported to Barb, not corrected here): the real RM role file sets
``permissions.lease/push_branch/open_pr`` to ``true``, but the same file's own
``instructions``/``scope.does_not`` say this role is report-only ("does not file GitHub
epics/issues or steal leases"). This port takes the safer, instruction-consistent reading
(``code_read_only=True``, ``lease``/``push_branch``/``open_pr`` all ``False``) rather than
granting an unattended scheduled role write capability its own instructions disclaim, matching
every other ported persona in this epic. RM's role file is out of this lane's file scope to fix.
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

ROLE = "pragmatic-programmer"

PRAGMATIC_PROGRAMMER_SCHEDULE_ENTRIES: Mapping[str, str] = {
    "weekly_review": "0 13 * * 5",
}

# Real staff/roles/pragmatic-programmer.yml lists 4 providers and "model: default" (no
# per-step override) -- preserved as-is rather than the 4-step chain with model overrides
# used by the invented personas, which do not have a prior real role file to match.
PRAGMATIC_PROGRAMMER_FAILOVER_CHAIN: tuple[FailoverStep, ...] = (
    FailoverStep("claude", None),
    FailoverStep("codex", None),
    FailoverStep("antigravity", None),
    FailoverStep("cursor-agent", None),
)


def pragmatic_programmer_role_data() -> dict[str, Any]:
    """Canonical staff/roles/pragmatic-programmer.yml mapping conforming to staff/schema.json (#3704)."""
    perms = dict.fromkeys(("lease", "push_branch", "open_pr", "merge", "host_shell", "notify_user"), False)
    perms["code_read_only"] = True
    chain = [
        {"provider": s.provider, **({"model": s.model} if s.model else {})} for s in PRAGMATIC_PROGRAMMER_FAILOVER_CHAIN
    ]
    return {
        "name": ROLE,
        "title": "Pragmatic Programmer",
        "summary": "Weekly craft/process review against The Pragmatic Programmer principles for the Board packet.",
        "playbook": "docs/operations/persona-cutover.md",
        "prompt_template": None,
        "instructions": (
            "You are Pragmatic Programmer, the fleet's weekly craft/process reviewer against The "
            "Pragmatic Programmer principles (DRY, orthogonality, reversibility, tracer bullets, "
            "design by contract, unbroken windows). Report-only: never files GitHub issues or PRs, "
            "posts leases, or starts implementation. Rotates one repo per week or does a portfolio "
            "skim. Schedule: 0 13 * * 5 (weekly_review, Friday 1 PM) in America/Los_Angeles. Reports "
            "to Barb in the interim, and to Board-Secretary for the Friday packet once the Board meets."
        ),
        "providers": ["claude", "codex", "antigravity", "cursor-agent"],
        "model": "default",
        "schedule": "0 13 * * 5",
        "window": None,
        "repos": ["UpstreamDrift", "AffineDrift", "Gasification_Model", "Tools", "Repository_Management"],
        "scope": {
            "schedules": dict(PRAGMATIC_PROGRAMMER_SCHEDULE_ENTRIES),
            "timezone": DEFAULT_TZ,
            "report_only": True,
            "rotation": "one repo per week or portfolio skim",
            "does_not": ["implement", "file-issues", "lease"],
        },
        "budget": {"usd_per_run": 4.0, "usd_per_day": 4.0},
        "permissions": perms,
        "retry": {"max_attempts": 4, "failover_chain": chain},
        "reports_to": "barb",
        "holds": [],
        "surface": "both",
        "persona": {
            "voice": "Craft reviewer. Names the principle first, then the exact line that strains it.",
            "greeting": "Pragmatic Programmer. Point me at code or a process and I will name the principle it strains.",
            "answers": [
                "which pragmatic principle a change or module strains",
                "whether an abstraction earns its keep",
                "where duplication has accumulated across repositories",
                "what the last craft review found and what moved since",
            ],
            "defers_to": ["fleet-critic", "cartographer", "board-secretary"],
        },
        "chat": {
            "contract": "staff/prompts/_chat_contract.md",
            "providers": ["claude", "codex", "antigravity", "cursor-agent"],
            "tools": ["read_repo", "search_code", "read_issue"],
        },
    }


def write_pragmatic_programmer_role_yaml(directory: Path) -> Path:
    """Write ``pragmatic-programmer.yml`` into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "pragmatic-programmer.yml"
    path.write_text(yaml.safe_dump(pragmatic_programmer_role_data(), sort_keys=False), encoding="utf-8")
    return path


def load_pragmatic_programmer_role_spec(directory: Path, *, raw: bool = False) -> Any:
    """Parse ``pragmatic-programmer.yml`` from ``directory`` into a ``RoleSpec`` (or raw dict when ``raw=True``)."""
    path = directory / "pragmatic-programmer.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if raw else read_and_parse_role_file(path)


def next_pragmatic_programmer_slots(after: datetime, spec: RoleSpec | None = None) -> dict[str, str]:
    """Compute the next fire timestamp in America/Los_Angeles for each Pragmatic Programmer schedule entry."""
    schedules = (
        dict(spec.scope.get("schedules") or PRAGMATIC_PROGRAMMER_SCHEDULE_ENTRIES)
        if spec is not None and isinstance(spec.scope, dict)
        else dict(PRAGMATIC_PROGRAMMER_SCHEDULE_ENTRIES)
    )
    return {job: next_fire(parse_cron(expr), after, tz=DEFAULT_TZ).isoformat() for job, expr in schedules.items()}


def build_pragmatic_programmer_capsule(
    *,
    last_review_date: str,
    findings: Sequence[Mapping[str, Any]],
    next_slots: Mapping[str, str],
    cursors: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Pragmatic Programmer capsule payload with review date, findings, and next slots."""
    items = [
        {
            "id": str(f.get("id") or f"finding-{i + 1}").strip(),
            "summary": str(f.get("summary") or "").strip(),
            "link": str(f.get("link") or "").strip(),
        }
        for i, f in enumerate(findings)
    ]
    date_s = str(last_review_date).strip()
    return {
        "last_scorecard_date": date_s,
        "findings": items,
        "open_items": [{"id": f["id"], "title": f["summary"], "url": f["link"]} for f in items],
        "cursors": dict(cursors or {"last_scorecard_date": date_s}),
        "next_slots": dict(next_slots),
    }


def validate_pragmatic_programmer_capsule(payload: Mapping[str, Any]) -> list[str]:
    """Return contract problems for a Pragmatic Programmer capsule payload (empty list when valid)."""
    problems: list[str] = []
    if not _DATE_RE.match(str(payload.get("last_scorecard_date") or "").strip()):
        problems.append("last_scorecard_date must be YYYY-MM-DD")
    findings = payload.get("findings")
    if not isinstance(findings, list):
        problems.append("findings must be a list")
    else:
        for i, item in enumerate(findings):
            if not isinstance(item, Mapping) or not str(item.get("summary") or "").strip():
                problems.append(f"findings[{i}].summary must be non-empty")
                continue
            if not str(item.get("link") or "").strip().startswith(("https://", "http://")):
                problems.append(f"findings[{i}].link must be a URL")
    slots = payload.get("next_slots")
    if not isinstance(slots, Mapping) or not set(PRAGMATIC_PROGRAMMER_SCHEDULE_ENTRIES).issubset(set(slots)):
        problems.append(f"next_slots must include {sorted(PRAGMATIC_PROGRAMMER_SCHEDULE_ENTRIES)}")
    return problems


def save_pragmatic_programmer_capsule(
    store: RoleCapsuleStore, payload: Mapping[str, Any], *, epoch: int = 1, now: float | None = None
) -> dict[str, Any]:
    """Validate Pragmatic Programmer's capsule schema and persist + mirror via RoleCapsuleStore."""
    if problems := validate_pragmatic_programmer_capsule(payload):
        raise ValueError(f"Invalid Pragmatic Programmer capsule: {'; '.join(problems)}")
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


def run_pragmatic_programmer_slot(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    *,
    job: str,
    slot: str,
    review_date: str,
    notify_opts: Mapping[str, Any],
    summary: str = "Pragmatic Programmer weekly review",
    body: str = "",
    findings: Sequence[Mapping[str, Any]] = (),
    holder: str = "rd:desk",
    epoch: int = 1,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute one scheduled Pragmatic Programmer slot unless fallback lease is active or already run.

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
    payload = build_pragmatic_programmer_capsule(
        last_review_date=review_date, findings=findings, next_slots=next_pragmatic_programmer_slots(clock)
    )
    saved = save_pragmatic_programmer_capsule(capsule_store, payload, epoch=epoch, now=clock.timestamp())
    msg = {
        "kind": "update",
        "urgency": "normal",
        "summary": summary,
        "body": body,
        "dedupe_key": f"{ROLE}-{job}-{slot}",
    }
    res = _notify.persona_notify(ROLE, msg, **notify_opts, clock=clock.timestamp)
    return {"status": "executed", "slot_key": key, "capsule": saved, "notify": res}


def run_pragmatic_programmer_fallback_drill(
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
    t0 = (base_time or datetime(2026, 11, 6, 12, 50, tzinfo=LA)).timestamp()
    dt = lambda offset: datetime.fromtimestamp(t0 + offset, tz=LA)  # noqa: E731

    def alert(key: str, summary: str, offset: float) -> dict[str, Any]:
        env = {"kind": "alert", "urgency": "high", "summary": summary, "body": summary, "dedupe_key": key}
        return _notify.persona_notify(
            ROLE, {**env, "recommended_action": "Monitor RD hub."}, **notify_opts, clock=lambda: t0 + offset
        )

    health_store.record_heartbeat(ROLE, epoch=1, now=t0)
    health_store.record_hub_probe(ROLE, ok=False, now=t0 + 600.0)
    unhealthy = health_store.record_hub_probe(ROLE, ok=False, now=t0 + 1200.0)
    init_cap = build_pragmatic_programmer_capsule(
        last_review_date=review_date, findings=[], next_slots=next_pragmatic_programmer_slots(dt(0))
    )
    save_pragmatic_programmer_capsule(capsule_store, init_cap, epoch=1, now=t0)
    lease_res = _fl.acquire_persona_lease(continuity_store, ROLE, holder=_fl.GROK_FALLBACK_HOLDER, now=dt(1200.0))
    fb_epoch = int((lease_res.get("lease") or lease_res)["epoch"])
    t_1 = alert(f"{ROLE}-takeover-{fb_epoch}", "Pragmatic Programmer took over on Grok Bot (hub unreachable)", 1200.0)
    t_2 = alert(f"{ROLE}-takeover-{fb_epoch}", "Pragmatic Programmer took over on Grok Bot (hub unreachable)", 1210.0)
    _fl.record_slot_claim(
        continuity_store,
        role=ROLE,
        job="weekly_review",
        slot=slot,
        holder=_fl.GROK_FALLBACK_HOLDER,
        epoch=fb_epoch,
        now=dt(1200.0),
    )
    fb_findings = [{"id": "finding-fb", "summary": "Verify RD hub", "link": "https://example.com/3704"}]
    fb_cap = build_pragmatic_programmer_capsule(
        last_review_date=review_date, findings=fb_findings, next_slots=next_pragmatic_programmer_slots(dt(1200.0))
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
    b_1 = alert(f"{ROLE}-back-{fb_epoch + 1}", "Pragmatic Programmer is back on RD", 2400.0)
    b_2 = alert(f"{ROLE}-back-{fb_epoch + 1}", "Pragmatic Programmer is back on RD", 2410.0)
    rd_attempt = run_pragmatic_programmer_slot(
        continuity_store,
        capsule_store,
        job="weekly_review",
        slot=slot,
        review_date=review_date,
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
