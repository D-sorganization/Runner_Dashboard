"""Expert-Panel RD persona role runtime, capsule schema, shadow-mode execution, and Grok Bot fallback drill (#3703).

Mirrors the Bob port (#3296), PM-Gasification (#3696) and Fleet-Dispatcher (#3701) ports:
report-only, one weekday slot, fleet-wide scope. Expert-Panel itself (the `/api/v1/staff/panels`
engine, #1634) is Dieter-initiated only and never self-starts a panel (docs/agents/grok.md Recipe
E: "never start one on your own initiative"), so this persona is a readiness/backlog digest over
panel infra health (open panel threads, stale/failed runs) rather than a panel-convening role.
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

ROLE = "expert-panel"

EXPERT_PANEL_SCHEDULE_ENTRIES: Mapping[str, str] = {
    "panel_readiness_digest": "0 9 * * 1-5",
}

EXPERT_PANEL_FAILOVER_CHAIN: tuple[FailoverStep, ...] = (
    FailoverStep("claude", "claude-opus-5-5"),
    FailoverStep("antigravity", "gemini-3.8-flash-high"),
    FailoverStep("cursor-agent", None),
    FailoverStep("codex", "gpt-6.1-sol"),
)


def expert_panel_role_data() -> dict[str, Any]:
    """Canonical staff/roles/expert-panel.yml mapping conforming to staff/schema.json (#3703)."""
    perms = dict.fromkeys(("lease", "push_branch", "open_pr", "merge", "host_shell"), False)
    perms.update({"notify_user": True, "code_read_only": True})
    chain = [{"provider": s.provider, **({"model": s.model} if s.model else {})} for s in EXPERT_PANEL_FAILOVER_CHAIN]
    return {
        "name": ROLE,
        "title": "Expert-Panel — Panel Readiness Digest",
        "summary": (
            "Report-only weekday readiness digest over expert-panel infra (open/stale panel "
            "threads via GET /api/v1/staff/panels). Never convenes a panel itself; panels stay "
            "Dieter-initiated via POST /api/v1/staff/panels (#1634)."
        ),
        "playbook": "docs/operations/persona-cutover.md",
        "prompt_template": None,
        "instructions": (
            "You are Expert-Panel. Report-only, never dispatches, opens PRs, merges, or starts a "
            "panel on your own initiative. Schedule: 0 9 * * 1-5 (panel_readiness_digest, weekday "
            "panel-infra readiness roundup) in America/Los_Angeles. Message Barb via persona_notify "
            "with open/stale panel thread state."
        ),
        "providers": ["claude", "antigravity", "cursor-agent", "codex"],
        "model": "claude-opus-5-5",
        "schedule": "0 9 * * 1-5",
        "window": None,
        "repos": ["Runner_Dashboard", "Repository_Management", "UpstreamDrift", "AffineDrift", "Gasification_Model"],
        "scope": {
            "schedules": dict(EXPERT_PANEL_SCHEDULE_ENTRIES),
            "timezone": DEFAULT_TZ,
            "report_only": True,
        },
        "budget": {"usd_per_run": 0.0, "usd_per_day": 0.0},
        "permissions": perms,
        "retry": {"max_attempts": 4, "failover_chain": chain},
        "reports_to": "barb",
        "holds": [],
        "surface": "both",
        "persona": {
            "voice": "Terse panel-infra reporter. Report-only, never starts a panel or opens PRs.",
            "greeting": "Expert-Panel here. Ask for the weekday panel-readiness digest.",
            "answers": ["0900 weekday panel-readiness digest", "open/stale panel thread state"],
            "defers_to": ["barb"],
        },
        "chat": {
            "contract": "staff/prompts/_chat_contract.md",
            "providers": ["claude", "antigravity", "codex"],
            "tools": ["read_briefing", "read_priorities", "read_sessions", "read_staff_summary", "read_run"],
        },
    }


def write_expert_panel_role_yaml(directory: Path) -> Path:
    """Write ``expert-panel.yml`` into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "expert-panel.yml"
    path.write_text(yaml.safe_dump(expert_panel_role_data(), sort_keys=False), encoding="utf-8")
    return path


def load_expert_panel_role_spec(directory: Path, *, raw: bool = False) -> Any:
    """Parse ``expert-panel.yml`` from ``directory`` into a ``RoleSpec`` (or raw dict when ``raw=True``)."""
    path = directory / "expert-panel.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if raw else read_and_parse_role_file(path)


def next_expert_panel_slots(after: datetime, spec: RoleSpec | None = None) -> dict[str, str]:
    """Compute the next fire timestamp in America/Los_Angeles for each Expert-Panel schedule entry."""
    schedules = (
        dict(spec.scope.get("schedules") or EXPERT_PANEL_SCHEDULE_ENTRIES)
        if spec is not None and isinstance(spec.scope, dict)
        else dict(EXPERT_PANEL_SCHEDULE_ENTRIES)
    )
    return {job: next_fire(parse_cron(expr), after, tz=DEFAULT_TZ).isoformat() for job, expr in schedules.items()}


def build_expert_panel_capsule(
    *,
    last_scorecard_date: str,
    open_blockers: Sequence[Mapping[str, Any]],
    next_slots: Mapping[str, str],
    cursors: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an Expert-Panel capsule payload with digest date, linked blockers, and next slots."""
    blockers = [
        {
            "id": str(b.get("id") or f"blocker-{i + 1}").strip(),
            "summary": str(b.get("summary") or "").strip(),
            "link": str(b.get("link") or "").strip(),
        }
        for i, b in enumerate(open_blockers)
    ]
    date_s = str(last_scorecard_date).strip()
    return {
        "last_scorecard_date": date_s,
        "open_blockers": blockers,
        "open_items": [{"id": b["id"], "title": b["summary"], "url": b["link"]} for b in blockers],
        "cursors": dict(cursors or {"last_scorecard_date": date_s}),
        "next_slots": dict(next_slots),
    }


def validate_expert_panel_capsule(payload: Mapping[str, Any]) -> list[str]:
    """Return contract problems for an Expert-Panel capsule payload (empty list when valid)."""
    problems: list[str] = []
    if not _DATE_RE.match(str(payload.get("last_scorecard_date") or "").strip()):
        problems.append("last_scorecard_date must be YYYY-MM-DD")
    blockers = payload.get("open_blockers")
    if not isinstance(blockers, list):
        problems.append("open_blockers must be a list")
    else:
        for i, item in enumerate(blockers):
            if not isinstance(item, Mapping) or not str(item.get("summary") or "").strip():
                problems.append(f"open_blockers[{i}].summary must be non-empty")
                continue
            if not str(item.get("link") or "").strip().startswith(("https://", "http://")):
                problems.append(f"open_blockers[{i}].link must be a URL")
    slots = payload.get("next_slots")
    if not isinstance(slots, Mapping) or not set(EXPERT_PANEL_SCHEDULE_ENTRIES).issubset(set(slots)):
        problems.append(f"next_slots must include {sorted(EXPERT_PANEL_SCHEDULE_ENTRIES)}")
    return problems


def save_expert_panel_capsule(
    store: RoleCapsuleStore, payload: Mapping[str, Any], *, epoch: int = 1, now: float | None = None
) -> dict[str, Any]:
    """Validate Expert-Panel's capsule schema and persist + mirror via RoleCapsuleStore."""
    if problems := validate_expert_panel_capsule(payload):
        raise ValueError(f"Invalid Expert-Panel capsule: {'; '.join(problems)}")
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


def run_expert_panel_slot(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    *,
    job: str,
    slot: str,
    digest_date: str,
    notify_opts: Mapping[str, Any],
    summary: str = "Expert-Panel weekday readiness digest",
    body: str = "",
    blockers: Sequence[Mapping[str, Any]] = (),
    holder: str = "rd:desk",
    epoch: int = 1,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute one scheduled Expert-Panel slot unless fallback lease is active or the slot already ran."""
    clock = now or datetime.now(LA)
    key = _fl.slot_ledger_key(ROLE, job, slot)
    if _fl.is_scheduler_blocked_by_fallback(continuity_store, ROLE, now=clock):
        return {"status": "skipped", "reason": "fallback_lease_held", "slot_key": key}
    if _fl.should_skip_slot(continuity_store, role=ROLE, job=job, slot=slot):
        return {"status": "skipped", "reason": "slot_already_executed", "slot_key": key}
    _fl.record_slot_claim(continuity_store, role=ROLE, job=job, slot=slot, holder=holder, epoch=epoch, now=clock)
    payload = build_expert_panel_capsule(
        last_scorecard_date=digest_date, open_blockers=blockers, next_slots=next_expert_panel_slots(clock)
    )
    saved = save_expert_panel_capsule(capsule_store, payload, epoch=epoch, now=clock.timestamp())
    msg = {
        "kind": "update",
        "urgency": "normal",
        "summary": summary,
        "body": body,
        "dedupe_key": f"{ROLE}-{job}-{slot}",
    }
    res = _notify.persona_notify(ROLE, msg, **notify_opts, clock=clock.timestamp)
    return {"status": "executed", "slot_key": key, "capsule": saved, "notify": res}


def run_expert_panel_fallback_drill(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    health_store: RoleHealthStore,
    *,
    slot: str,
    digest_date: str,
    notify_opts: Mapping[str, Any],
    base_time: datetime | None = None,
) -> dict[str, Any]:
    """Run the staging fallback drill: hub down -> Grok takeover -> digest slot -> resume -> RD skip."""
    t0 = (base_time or datetime(2026, 11, 1, 8, 50, tzinfo=LA)).timestamp()
    dt = lambda offset: datetime.fromtimestamp(t0 + offset, tz=LA)  # noqa: E731

    def alert(key: str, summary: str, offset: float) -> dict[str, Any]:
        env = {"kind": "alert", "urgency": "high", "summary": summary, "body": summary, "dedupe_key": key}
        return _notify.persona_notify(
            ROLE, {**env, "recommended_action": "Monitor RD hub."}, **notify_opts, clock=lambda: t0 + offset
        )

    health_store.record_heartbeat(ROLE, epoch=1, now=t0)
    health_store.record_hub_probe(ROLE, ok=False, now=t0 + 600.0)
    unhealthy = health_store.record_hub_probe(ROLE, ok=False, now=t0 + 1200.0)
    init_cap = build_expert_panel_capsule(
        last_scorecard_date=digest_date, open_blockers=[], next_slots=next_expert_panel_slots(dt(0))
    )
    save_expert_panel_capsule(capsule_store, init_cap, epoch=1, now=t0)
    lease_res = _fl.acquire_persona_lease(continuity_store, ROLE, holder=_fl.GROK_FALLBACK_HOLDER, now=dt(1200.0))
    fb_epoch = int((lease_res.get("lease") or lease_res)["epoch"])
    t_1 = alert(f"{ROLE}-takeover-{fb_epoch}", "Expert-Panel took over on Grok Bot (hub unreachable)", 1200.0)
    t_2 = alert(f"{ROLE}-takeover-{fb_epoch}", "Expert-Panel took over on Grok Bot (hub unreachable)", 1210.0)
    _fl.record_slot_claim(
        continuity_store,
        role=ROLE,
        job="panel_readiness_digest",
        slot=slot,
        holder=_fl.GROK_FALLBACK_HOLDER,
        epoch=fb_epoch,
        now=dt(1200.0),
    )
    fb_blockers = [{"id": "blocker-fb", "summary": "Verify RD hub", "link": "https://example.com/3703"}]
    fb_cap = build_expert_panel_capsule(
        last_scorecard_date=digest_date, open_blockers=fb_blockers, next_slots=next_expert_panel_slots(dt(1200.0))
    )
    fb_slots = [f"{k}:{v}" for k, v in sorted(fb_cap["next_slots"].items())]
    capsule_store.ingest_fallback_capsule(
        ROLE,
        {
            "role": ROLE,
            "epoch": fb_epoch + 1,
            "last_scorecard_date": digest_date,
            "open_items": fb_cap["open_items"],
            "cursors": fb_cap["cursors"],
            "next_slots": fb_slots,
        },
        now=t0 + 1500.0,
    )
    health_store.record_heartbeat(ROLE, epoch=fb_epoch + 1, now=t0 + 1800.0)
    resumed = health_store.record_heartbeat(ROLE, epoch=fb_epoch + 1, now=t0 + 2400.0)
    _fl.release_persona_lease(continuity_store, ROLE, holder=_fl.GROK_FALLBACK_HOLDER, epoch=fb_epoch, now=dt(2400.0))
    b_1 = alert(f"{ROLE}-back-{fb_epoch + 1}", "Expert-Panel is back on RD", 2400.0)
    b_2 = alert(f"{ROLE}-back-{fb_epoch + 1}", "Expert-Panel is back on RD", 2410.0)
    rd_attempt = run_expert_panel_slot(
        continuity_store,
        capsule_store,
        job="panel_readiness_digest",
        slot=slot,
        digest_date=digest_date,
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
