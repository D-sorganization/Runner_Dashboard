"""Shadow-mode provider advice from verified outcomes (epic #2117). Never changes the choice.

When ``STAFF_ROUTING_MODE=shadow`` (default ``off``), each newly admitted run gets one
``routing_shadow`` event ``{chosen, recommended, scores, ...}`` comparing the provider the
failover chain picked with the empirical best from the report cards. A provider is only
scored once it has ``MIN_ROUTING_SAMPLE`` quality-scored runs (success vs quality_fail;
no-ops and infra failures are excluded, infra is reported as ``availability``); with no
such provider ``recommended`` is ``None``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from staff import outcomes
from staff.report_cards import build_report_cards, infer_complexity_tier
from staff.store import RunRecord, RunStore

log = logging.getLogger("dashboard.staff.routing_shadow")

ENV_MODE = "STAFF_ROUTING_MODE"
EVENT_KIND = "routing_shadow"
MIN_ROUTING_SAMPLE = 8
_HISTORY_LIMIT = 2000


def mode() -> str:
    """``shadow`` or ``off`` (anything else is off)."""
    return "shadow" if os.environ.get(ENV_MODE, "").strip().lower() == "shadow" else "off"


def advise(runs: list[RunRecord], *, role: str, chosen: str, complexity_tier: str) -> dict[str, Any]:
    """Pure: compare ``chosen`` with the best provider that has n >= MIN_ROUTING_SAMPLE for the role/tier."""
    cards = build_report_cards(runs, role_filter=role)["role_tier_breakdown"]
    best_per_provider: dict[str, dict[str, Any]] = {}
    for c in cards:
        if c["complexity_tier"] != complexity_tier:
            continue
        cur = best_per_provider.get(c["provider"])
        if cur is None or c["terminal_runs"] > cur["terminal_runs"]:
            best_per_provider[c["provider"]] = c
    scores = {
        prov: {
            "score": c["leaderboard_score"],
            "n": c["terminal_runs"],
            "first_pass_ci_95": c["first_pass_ci_95"],
            "availability": c["availability"],
        }
        for prov, c in sorted(best_per_provider.items())
    }
    eligible = [(p, c) for p, c in best_per_provider.items() if c["terminal_runs"] >= MIN_ROUTING_SAMPLE]
    eligible.sort(key=lambda pc: (-float(pc[1]["leaderboard_score"]), pc[0]))
    recommended = eligible[0][0] if eligible else None
    return {
        "role": role,
        "complexity_tier": complexity_tier,
        "chosen": chosen,
        "recommended": recommended,
        "agrees": None if recommended is None else recommended == chosen,
        "min_n": MIN_ROUTING_SAMPLE,
        "scores": scores,
    }


def record_for_run(store: RunStore, rec: RunRecord) -> dict[str, Any] | None:
    """Append a ``routing_shadow`` event for ``rec`` when mode is shadow. Never raises, never reroutes."""
    if mode() != "shadow":
        return None
    try:
        history = [
            r
            for r in store.list_runs(limit=_HISTORY_LIMIT, since=outcomes.default_since())
            if r.id != rec.id and r.role == rec.role
        ]
        advice = advise(history, role=rec.role, chosen=rec.provider, complexity_tier=infer_complexity_tier(rec))
        store.append_event(rec.id, EVENT_KIND, json.dumps(advice, sort_keys=True))
        return advice
    except Exception:  # noqa: BLE001 - advisory only; must never affect dispatch
        log.warning("routing shadow advice failed for %s", rec.id, exc_info=True)
        return None
