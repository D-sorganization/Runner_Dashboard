"""Zero-LLM-cost empirical routing leaderboard and agent recommendation helper (#2284)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from staff import outcomes
from staff.outcomes import PrFact, PrKey
from staff.report_cards import (
    MIN_CONFIDENT_SAMPLE,
    build_report_cards,
    infer_complexity_tier,
    wilson_ci_95,
)
from staff.store import RunRecord

__all__ = [
    "MIN_CONFIDENT_SAMPLE",
    "build_report_cards",
    "infer_complexity_tier",
    "normalize_complexity",
    "recommend_agent",
    "wilson_ci_95",
]

_COMPLEXITY_ALIASES = {
    "low": "low",
    "simple": "low",
    "easy": "low",
    "trivial": "low",
    "medium": "medium",
    "med": "medium",
    "moderate": "medium",
    "normal": "medium",
    "high": "high",
    "complex": "high",
    "hard": "high",
}


def normalize_complexity(complexity: str | None) -> str:
    """Normalize a caller complexity string into low, medium, or high."""
    raw = (complexity or "medium").strip().lower()
    return _COMPLEXITY_ALIASES.get(raw, "medium")


def _load_default_runs(since: str | None = None, limit: int = 2000) -> list[RunRecord]:
    try:
        from staff.runner import get_runner

        start = since or outcomes.default_since()
        return list(get_runner().store.list_runs(limit=limit, since=start))
    except Exception:  # noqa: BLE001
        return []


def recommend_agent(
    role: str = "implementer",
    repo: str = "",
    complexity: str = "medium",
    *,
    runs: Sequence[RunRecord] | None = None,
    facts: Mapping[PrKey, PrFact] | None = None,
    available_providers: Sequence[str] = ("claude", "codex", "antigravity"),
    since: str | None = None,
) -> dict[str, Any]:
    """Recommend the highest-yield, lowest-waste agent (provider + model) for (role, repo, complexity).

    Uses repo-scoped empirical runs first when confident data exists, then falls back
    to fleet-wide role+complexity empirical data, then overall leaderboard, then heuristic.
    """
    norm_role = (role or "implementer").strip().lower()
    norm_repo = (repo or "").strip()
    tier = normalize_complexity(complexity)
    dataset = list(runs) if runs is not None else _load_default_runs(since=since)

    if norm_repo:
        repo_runs = [r for r in dataset if (r.repo or "").lower() == norm_repo.lower()]
        if repo_runs:
            repo_report = build_report_cards(
                repo_runs,
                facts,
                available_providers=available_providers,
                role_filter=norm_role,
            )
            for rec in repo_report.get("routing_recommendations", []):
                if rec.get("role") == norm_role and rec.get("complexity_tier") == tier:
                    if rec.get("confidence") != "heuristic_fallback":
                        return {**rec, "repo": norm_repo, "scope": "repo_empirical"}

    fleet_report = build_report_cards(
        dataset,
        facts,
        available_providers=available_providers,
        role_filter=norm_role,
    )
    for rec in fleet_report.get("routing_recommendations", []):
        if rec.get("role") == norm_role and rec.get("complexity_tier") == tier:
            return {**rec, "repo": norm_repo, "scope": "fleet_role_empirical"}

    full_report = build_report_cards(dataset, facts, available_providers=available_providers)
    avail_set = {p.strip().lower() for p in available_providers if p}
    viable = [
        c
        for c in full_report.get("leaderboard", [])
        if c["provider"] in avail_set
        and "zero_yield_token_sink" not in c.get("flags", [])
        and "tier_mismatch_waste" not in c.get("flags", [])
        and (c.get("first_pass_success_rate") or 0.0) > 0.0
    ]
    if viable:
        best = viable[0]
        fallback = viable[1]["provider"] if len(viable) > 1 else ("codex" if best["provider"] != "codex" else "claude")
        return {
            "role": norm_role,
            "repo": norm_repo,
            "complexity_tier": tier,
            "recommended_provider": best["provider"],
            "recommended_model": best["model"],
            "fallback_provider": fallback,
            "confidence": "high" if best["terminal_runs"] >= MIN_CONFIDENT_SAMPLE else "low_sample",
            "avoid_models": [
                f"{c['provider']}/{c['model']}"
                for c in full_report.get("leaderboard", [])
                if "zero_yield_token_sink" in c.get("flags", []) or "tier_mismatch_waste" in c.get("flags", [])
            ],
            "rationale": (
                f"{best['provider']}/{best['model']} leads overall fleet leaderboard with "
                f"score {best['leaderboard_score']} (first-pass {best['first_pass_success_rate']})."
            ),
            "scope": "fleet_overall_empirical",
        }

    default_prov = "claude" if "claude" in avail_set else next(iter(avail_set), "claude")
    return {
        "role": norm_role,
        "repo": norm_repo,
        "complexity_tier": tier,
        "recommended_provider": default_prov,
        "recommended_model": "default",
        "fallback_provider": "codex" if default_prov != "codex" else "claude",
        "confidence": "heuristic_fallback",
        "avoid_models": [],
        "rationale": f"No passing empirical runs for {norm_role} ({tier}); defaulting to {default_prov}.",
        "scope": "heuristic",
    }
