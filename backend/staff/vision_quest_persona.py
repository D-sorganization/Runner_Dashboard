"""Vision Quest RD persona role runtime, capsule schema, shadow-mode execution, and Grok Bot
fallback drill (#3704). The real yaml has no ``retry`` block, so none is fabricated here --
provider selection for rd-barb relies on the static ``PERSONA_ROLE_CHAINS`` entry.
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

ROLE = "vision-quest"
VISION_QUEST_SCHEDULE_ENTRIES: Mapping[str, str] = {"monthly_quest": "0 9 1 * *"}

VISION_QUEST_REPOS: tuple[str, ...] = ("UpstreamDrift", "AffineDrift", "Gasification_Model", "Tools")
VISION_QUEST_LENSES: tuple[str, ...] = ("theorist", "experimentalist", "transplanter", "contrarian")
VISION_QUEST_GROUNDINGS: tuple[str, ...] = ("established", "inferred", "speculative")


def vision_quest_role_data() -> dict[str, Any]:
    """Canonical staff/roles/vision-quest.yml mapping conforming to staff/schema.json (#3704)."""
    perms = dict.fromkeys(("lease", "merge", "host_shell", "notify_user"), False)
    perms["push_branch"] = True
    perms["open_pr"] = True
    return {
        "name": ROLE,
        "title": "Vision Quest",
        "summary": (
            "Innovation panel of frontier experts that turns our compiled results into avenues for the owner "
            "to pursue; advises only and stays out of operations."
        ),
        "playbook": "docs/fleet-vision-quest.md",
        "prompt_template": "docs/templates/vision-quest-brief.md",
        "instructions": (
            "Convenes a panel of frontier-model seats to advise the owner on vision and direction. Decides "
            "nothing: reports to the owner, never the Board, has no submit_proposal tool, and files no issues, "
            "proposals or leases. An avenue enters operations only via an owner Board proposal citing the "
            "brief. Briefs archive under docs/vision-quest/YYYY-MM-DD-<topic>/ in Repository_Management, "
            "never in the Board archives.\n\nPhases: the Disciple's brief; independent frontier memos under lenses "
            "(theorist, experimentalist, transplanter, contrarian); a cross-pollination round; a grounding "
            "pass where Fleet Critic tags every claim established, inferred, or speculative; the owner brief, "
            "with three to five ranked avenues. A seat whose provider is out of usage is skipped; quorum is 2. "
            "Real seats only (owner decision 2026-10-23): phases 2-4 run on the Runner Dashboard expert panel, "
            "POST /api/v1/staff/panels on the hub holding findings and cartographer packs. The evidence brief "
            "comes from the Disciple or GET /api/v1/staff/knowledge/findings/search, never from the chair "
            "reading files alone; memos.md/brief.md cite the panel thread ids. A chair that cannot reach the "
            "panel does not write lens memos itself: it stops, or labels the output single-seat."
        ),
        "providers": ["claude", "antigravity", "codex", "cursor-agent"],
        "model": "default",
        "schedule": "0 9 1 * *",
        "window": None,
        "repos": list(VISION_QUEST_REPOS),
        "scope": {
            "schedules": dict(VISION_QUEST_SCHEDULE_ENTRIES),
            "archive": "docs/vision-quest",
            "pack": "staff/knowledge/findings.yml",
            "quorum": 2,
            "avenues_per_brief": "3-5",
            "panel_endpoint": "/api/v1/staff/panels",
            "panel_hub": "http://deskcomputer:8321",
            "evidence_search": "/api/v1/staff/knowledge/findings/search",
            "does_not": ["decide", "file-issues", "submit-proposals", "implement", "lease"],
        },
        "budget": {"usd_per_run": 15.0, "usd_per_day": 15.0},
        "permissions": perms,
        "reports_to": "user",
        "holds": [],
        "surface": "dashboard",
        "persona": {
            "voice": (
                "Frontier scout for the owner. Divergent first, then honest about which ideas are grounded "
                "and which are speculative."
            ),
            "greeting": "Vision Quest. Give me a topic and I will convene the panel on where our results go next.",
            "answers": [
                "which research avenues our results suggest pursuing next",
                "what an adjacent field has solved that we could transplant",
                "which consensus assumption in our work is worth challenging",
                "the cheapest first experiment that would open an avenue",
            ],
            "defers_to": ["disciple", "research-scout", "fleet-critic"],
        },
        "chat": {
            "contract": "staff/prompts/_chat_contract.md",
            "providers": ["claude", "antigravity", "codex"],
            "tools": ["search_knowledge", "read_repo", "read_issue"],
        },
    }


def write_vision_quest_role_yaml(directory: Path) -> Path:
    """Write ``vision-quest.yml`` into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "vision-quest.yml"
    path.write_text(yaml.safe_dump(vision_quest_role_data(), sort_keys=False), encoding="utf-8")
    return path


def load_vision_quest_role_spec(directory: Path, *, raw: bool = False) -> Any:
    """Parse ``vision-quest.yml`` from ``directory`` into a ``RoleSpec`` (or raw dict when ``raw=True``)."""
    path = directory / "vision-quest.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if raw else read_and_parse_role_file(path)


def next_vision_quest_slots(after: datetime, spec: RoleSpec | None = None) -> dict[str, str]:
    """Compute the next fire timestamp in America/Los_Angeles for each Vision Quest schedule entry."""
    schedules = (
        dict(spec.scope.get("schedules") or VISION_QUEST_SCHEDULE_ENTRIES)
        if spec is not None and isinstance(spec.scope, dict)
        else dict(VISION_QUEST_SCHEDULE_ENTRIES)
    )
    return {job: next_fire(parse_cron(expr), after, tz=DEFAULT_TZ).isoformat() for job, expr in schedules.items()}


def build_vision_quest_capsule(
    *,
    last_quest_date: str,
    avenues: Sequence[Mapping[str, Any]],
    next_slots: Mapping[str, str],
    cursors: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Vision Quest capsule payload with the quest date, ranked avenues, and next slots."""
    items = [
        {
            "lens": str(a.get("lens") or "").strip(),
            "title": str(a.get("title") or "").strip(),
            "grounding": str(a.get("grounding") or "speculative").strip(),
            "link": str(a.get("link") or "").strip(),
        }
        for a in avenues
    ]
    date_s = str(last_quest_date).strip()
    return {
        "last_scorecard_date": date_s,
        "avenues": items,
        "open_items": [{"id": f"avenue-{i}", "title": a["title"], "url": a["link"]} for i, a in enumerate(items)],
        "cursors": dict(cursors or {"last_scorecard_date": date_s}),
        "next_slots": dict(next_slots),
    }


def validate_vision_quest_capsule(payload: Mapping[str, Any]) -> list[str]:
    """Return contract problems for a Vision Quest capsule payload (empty list when valid)."""
    problems: list[str] = []
    if not _DATE_RE.match(str(payload.get("last_scorecard_date") or "").strip()):
        problems.append("last_scorecard_date must be YYYY-MM-DD")
    avenues = payload.get("avenues")
    if not isinstance(avenues, list):
        problems.append("avenues must be a list")
    else:
        for i, a in enumerate(avenues):
            if not isinstance(a, Mapping) or not str(a.get("title") or "").strip():
                problems.append(f"avenues[{i}].title must be non-empty")
                continue
            if str(a.get("lens") or "").strip() not in VISION_QUEST_LENSES:
                problems.append(f"avenues[{i}].lens must be one of {list(VISION_QUEST_LENSES)}")
            if str(a.get("grounding") or "").strip() not in VISION_QUEST_GROUNDINGS:
                problems.append(f"avenues[{i}].grounding must be one of {list(VISION_QUEST_GROUNDINGS)}")
            if not str(a.get("link") or "").strip().startswith(("https://", "http://")):
                problems.append(f"avenues[{i}].link must be a URL")
    slots = payload.get("next_slots")
    if not isinstance(slots, Mapping) or not set(VISION_QUEST_SCHEDULE_ENTRIES).issubset(set(slots)):
        problems.append(f"next_slots must include {sorted(VISION_QUEST_SCHEDULE_ENTRIES)}")
    return problems


def save_vision_quest_capsule(
    store: RoleCapsuleStore, payload: Mapping[str, Any], *, epoch: int = 1, now: float | None = None
) -> dict[str, Any]:
    """Validate Vision Quest's capsule schema and persist + mirror via RoleCapsuleStore."""
    if problems := validate_vision_quest_capsule(payload):
        raise ValueError(f"Invalid Vision Quest capsule: {'; '.join(problems)}")
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


def run_vision_quest_slot(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    *,
    job: str,
    slot: str,
    quest_date: str,
    notify_opts: Mapping[str, Any],
    summary: str = "Vision Quest monthly brief",
    body: str = "",
    avenues: Sequence[Mapping[str, Any]] = (),
    holder: str = "rd:desk",
    epoch: int = 1,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute one scheduled Vision Quest slot unless fallback lease is active or already run."""
    clock = now or datetime.now(LA)
    key = _fl.slot_ledger_key(ROLE, job, slot)
    if _fl.is_scheduler_blocked_by_fallback(continuity_store, ROLE, now=clock):
        return {"status": "skipped", "reason": "fallback_lease_held", "slot_key": key}
    if _fl.should_skip_slot(continuity_store, role=ROLE, job=job, slot=slot):
        return {"status": "skipped", "reason": "slot_already_executed", "slot_key": key}
    _fl.record_slot_claim(continuity_store, role=ROLE, job=job, slot=slot, holder=holder, epoch=epoch, now=clock)
    payload = build_vision_quest_capsule(
        last_quest_date=quest_date, avenues=avenues, next_slots=next_vision_quest_slots(clock)
    )
    saved = save_vision_quest_capsule(capsule_store, payload, epoch=epoch, now=clock.timestamp())
    msg = {
        "kind": "update",
        "urgency": "normal",
        "summary": summary,
        "body": body,
        "dedupe_key": f"{ROLE}-{job}-{slot}",
    }
    res = _notify.persona_notify(ROLE, msg, **notify_opts, clock=clock.timestamp)
    return {"status": "executed", "slot_key": key, "capsule": saved, "notify": res}


def run_vision_quest_fallback_drill(
    continuity_store: BarbContinuityStore,
    capsule_store: RoleCapsuleStore,
    health_store: RoleHealthStore,
    *,
    slot: str,
    quest_date: str,
    notify_opts: Mapping[str, Any],
    base_time: datetime | None = None,
) -> dict[str, Any]:
    """Run the staging fallback drill: hub down -> Grok takeover -> monthly brief -> resume -> RD skip."""
    t0 = (base_time or datetime(2026, 12, 1, 9, 50, tzinfo=LA)).timestamp()
    dt = lambda offset: datetime.fromtimestamp(t0 + offset, tz=LA)  # noqa: E731

    def alert(key: str, summary: str, offset: float) -> dict[str, Any]:
        env = {"kind": "alert", "urgency": "high", "summary": summary, "body": summary, "dedupe_key": key}
        return _notify.persona_notify(
            ROLE, {**env, "recommended_action": "Monitor RD hub."}, **notify_opts, clock=lambda: t0 + offset
        )

    health_store.record_heartbeat(ROLE, epoch=1, now=t0)
    health_store.record_hub_probe(ROLE, ok=False, now=t0 + 600.0)
    unhealthy = health_store.record_hub_probe(ROLE, ok=False, now=t0 + 1200.0)
    init_cap = build_vision_quest_capsule(
        last_quest_date=quest_date, avenues=[], next_slots=next_vision_quest_slots(dt(0))
    )
    save_vision_quest_capsule(capsule_store, init_cap, epoch=1, now=t0)
    lease_res = _fl.acquire_persona_lease(continuity_store, ROLE, holder=_fl.GROK_FALLBACK_HOLDER, now=dt(1200.0))
    fb_epoch = int((lease_res.get("lease") or lease_res)["epoch"])
    msg = "Vision Quest took over on Grok Bot"
    takeovers = [alert(f"{ROLE}-takeover-{fb_epoch}", msg, o) for o in (1200.0, 1210.0)]
    _fl.record_slot_claim(
        continuity_store,
        role=ROLE,
        job="monthly_quest",
        slot=slot,
        holder=_fl.GROK_FALLBACK_HOLDER,
        epoch=fb_epoch,
        now=dt(1200.0),
    )
    fb_avenues = [
        {
            "lens": "contrarian",
            "title": "Verify RD hub before trusting panel output",
            "grounding": "speculative",
            "link": "https://example.com/3704",
        }
    ]
    fb_cap = build_vision_quest_capsule(
        last_quest_date=quest_date, avenues=fb_avenues, next_slots=next_vision_quest_slots(dt(1200.0))
    )
    fb_slots = [f"{k}:{v}" for k, v in sorted(fb_cap["next_slots"].items())]
    capsule_store.ingest_fallback_capsule(
        ROLE,
        {
            "role": ROLE,
            "epoch": fb_epoch + 1,
            "last_scorecard_date": quest_date,
            "open_items": fb_cap["open_items"],
            "cursors": fb_cap["cursors"],
            "next_slots": fb_slots,
        },
        now=t0 + 1500.0,
    )
    health_store.record_heartbeat(ROLE, epoch=fb_epoch + 1, now=t0 + 1800.0)
    resumed = health_store.record_heartbeat(ROLE, epoch=fb_epoch + 1, now=t0 + 2400.0)
    _fl.release_persona_lease(continuity_store, ROLE, holder=_fl.GROK_FALLBACK_HOLDER, epoch=fb_epoch, now=dt(2400.0))
    backs = [alert(f"{ROLE}-back-{fb_epoch + 1}", "Vision Quest is back on RD", o) for o in (2400.0, 2410.0)]
    rd_attempt = run_vision_quest_slot(
        continuity_store,
        capsule_store,
        job="monthly_quest",
        slot=slot,
        quest_date=quest_date,
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
