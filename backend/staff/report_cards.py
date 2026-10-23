"""Zero-LLM-cost Agent Report Cards, Bean-Counter Token Ledger & Empirical Leaderboard.

Computes empirical coding performance, rework/bug rates, effective token cost per
successful deliverable/merge, token-waste diagnostics (``bean-counter``), and
task-complexity routing recommendations directly from SQLite ``runs`` rows plus
cached PR facts — zero extra LLM invocations required (panel ``th_5e95506efbf0``).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from staff.outcomes import PrFact, PrKey, full_repo, pr_key, rate
from staff.run_outcome import classify_outcome
from staff.store import ACTIVE_STATUSES, RunRecord

DEFAULT_REPORT_WINDOW_DAYS = 14
MIN_CONFIDENT_SAMPLE = 5
COMPLEX_ROLES = frozenset({"chief-architect", "mad-scientist", "fleet-critic", "code-reviewer"})
STANDARD_ROLES = frozenset(
    {
        "issue-remediator",
        "pr-remediator",
        "pragmatic-programmer",
        "test-hygiene",
        "ad-hoc",
        "maintenance",
        "tooling-scout",
        "bean-counter",
    }
)
CODING_ROLES = frozenset(
    {"issue-remediator", "pr-remediator", "pragmatic-programmer", "test-hygiene", "ad-hoc", "night-watch", "sanitation"}
)
LOW_TIER_TOKENS = ("flash", "mini", "haiku", "nano", "small", "lite")


def default_report_since(days: int = DEFAULT_REPORT_WINDOW_DAYS, now: datetime | None = None) -> str:
    """Midnight UTC ``days`` ago as ISO-8601 Z timestamp."""
    start = (now or datetime.now(UTC)) - timedelta(days=max(1, days))
    return start.replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def infer_complexity_tier(rec: RunRecord) -> str:
    """Resolve task complexity tier from explicit ``model_tier`` or role classification."""
    raw = (rec.model_tier or "").strip().lower()
    if raw in ("routine", "standard", "complex", "strong"):
        return "complex" if raw == "strong" else raw
    if rec.role in COMPLEX_ROLES:
        return "complex"
    return "standard" if rec.role in STANDARD_ROLES else "routine"


def wilson_ci_95(successes: int, total: int) -> tuple[float, float] | None:
    """95% Wilson score confidence interval ``(low, high)`` for a binomial proportion."""
    if total <= 0:
        return None
    z = 1.959964
    phat = successes / total
    denom = 1.0 + (z * z) / total
    center = (phat + (z * z) / (2.0 * total)) / denom
    half = (z * math.sqrt((phat * (1.0 - phat) + (z * z) / (4.0 * total)) / total)) / denom
    return (round(max(0.0, center - half), 4), round(min(1.0, center + half), 4))


def _norm_model(rec: RunRecord) -> str:
    return (rec.model or "").strip() or "default"


def _is_low_tier_model(provider: str, model: str) -> bool:
    combined = f"{provider}:{model}".lower()
    return any(tok in combined for tok in LOW_TIER_TOKENS) or provider == "antigravity"


def _is_clean_success(rec: RunRecord) -> bool:
    """Success = succeeded AND verification ``verified`` (a clean exit and no failure class)."""
    if rec.exit_code is not None and int(rec.exit_code) != 0:
        return False
    if (rec.failure_class or "").strip():
        return False
    return classify_outcome(rec.status, rec.verification, rec.failure_class) == "success"


def _remediated_targets(runs: Sequence[RunRecord]) -> tuple[set[PrKey], set[tuple[str, str]]]:
    """Identify PRs and branches that required a subsequent ``pr-remediator`` run."""
    pr_targets: set[PrKey] = set()
    branch_targets: set[tuple[str, str]] = set()
    for r in runs:
        if r.role != "pr-remediator":
            continue
        pk = pr_key(r)
        if pk is not None:
            pr_targets.add(pk)
        if r.repo and r.target_kind == "pr" and r.target_ref.isdigit():
            pr_targets.add((full_repo(r.repo), int(r.target_ref)))
        if r.repo and r.branch:
            branch_targets.add((full_repo(r.repo), r.branch.strip()))
    return pr_targets, branch_targets


def _needs_remediation(
    rec: RunRecord,
    pr_targets: set[PrKey],
    branch_targets: set[tuple[str, str]],
    facts: Mapping[PrKey, PrFact],
) -> bool:
    if rec.role == "pr-remediator":
        return False
    pk = pr_key(rec)
    if pk is not None:
        if pk in pr_targets:
            return True
        fact = facts.get(pk)
        if fact is not None and (fact.followup_fix is True or fact.ci_first_pass is False):
            return True
    return bool(rec.repo and rec.branch and (full_repo(rec.repo), rec.branch.strip()) in branch_targets)


def _score_and_grade(
    terminal: int,
    first_pass_rate: float | None,
    ci_95: tuple[float, float] | None,
    rework_rate_val: float | None,
    eff_tokens_first_pass: float | None,
) -> tuple[float, str]:
    """Composite empirical score (0..100) and letter grade using Wilson lower bound."""
    if terminal <= 0 or first_pass_rate is None:
        return 0.0, "I"
    wilson_low = ci_95[0] if ci_95 is not None else first_pass_rate
    rework_clean = 1.0 - min(1.0, rework_rate_val or 0.0)
    if eff_tokens_first_pass is None or eff_tokens_first_pass <= 0:
        token_eff = 0.0 if first_pass_rate == 0 else 0.75
    else:
        token_eff = 1.0 / (1.0 + (eff_tokens_first_pass / 120_000.0))
    maturity = min(1.0, terminal / 8.0)
    score = round(
        max(0.0, min(100.0, 100.0 * (0.45 * wilson_low + 0.25 * rework_clean + 0.20 * token_eff + 0.10 * maturity))), 1
    )
    if terminal < 3:
        return score, "I"
    for threshold, letter in ((75.0, "A"), (60.0, "B"), (45.0, "C"), (25.0, "D")):
        if score >= threshold:
            return score, letter
    return score, "F"


def _build_card(
    runs: Sequence[RunRecord],
    pr_targets: set[PrKey],
    branch_targets: set[tuple[str, str]],
    facts: Mapping[PrKey, PrFact],
    *,
    provider: str,
    model: str,
    role: str = "*",
    complexity_tier: str = "*",
) -> dict[str, Any]:
    in_flight = sum(1 for r in runs if r.status in ACTIVE_STATUSES)
    # Neutral (no-op / cancelled) and infra-failed runs are excluded from the quality denominator (n).
    buckets = {r.id: classify_outcome(r.status, r.verification, r.failure_class) for r in runs}
    neutral = sum(1 for b in buckets.values() if b in ("no_op", "cancelled"))
    infra_failed = sum(1 for b in buckets.values() if b == "infra_fail")
    term_runs = [r for r in runs if buckets[r.id] in ("success", "quality_fail")]
    terminal = len(term_runs)
    succeeded = first_pass = failed = retried = remediation_followups = wasted_tokens = prs_merged_count = 0
    failure_breakdown: dict[str, int] = {}
    pr_keys_opened: set[PrKey] = set()

    for r in term_runs:
        clean = _is_clean_success(r)
        is_retry = int(r.attempt or 1) > 1 or bool((r.retry_of or "").strip())
        rem_needed = _needs_remediation(r, pr_targets, branch_targets, facts)
        run_tok = int(r.input_tokens or 0) + int(r.output_tokens or 0)
        if is_retry:
            retried += 1
        if rem_needed:
            remediation_followups += 1
        if clean:
            succeeded += 1
            if not is_retry and not rem_needed:
                first_pass += 1
            elif is_retry:
                wasted_tokens += run_tok // 2
        else:
            failed += 1
            wasted_tokens += run_tok
            reason = (r.failure_class or "").strip() or (
                f"verification:{r.verification}" if r.verification in ("failed", "unverified") else r.status
            )
            failure_breakdown[reason] = failure_breakdown.get(reason, 0) + 1
        pk = pr_key(r)
        if pk is not None:
            pr_keys_opened.add(pk)

    prs_merged_count += sum(1 for pk in pr_keys_opened if facts.get(pk) is not None and facts[pk].state == "merged")
    prs_merged_count += sum(
        1 for r in term_runs if pr_key(r) is None and (r.artifact == "pr_merged" or r.outcome == "merged")
    )
    prs_opened = len(pr_keys_opened) + sum(
        1 for r in term_runs if pr_key(r) is None and r.artifact in ("pr_open", "pr_merged")
    )
    coding_dispatches = sum(1 for r in term_runs if r.role in CODING_ROLES or r.target_kind in ("issue", "pr"))

    in_tok = sum(int(r.input_tokens or 0) for r in runs)
    out_tok = sum(int(r.output_tokens or 0) for r in runs)
    std_tok = sum(int(r.standards_tokens or 0) for r in runs)
    tot_tok = in_tok + out_tok
    cost = round(sum(float(r.cost_usd or 0.0) for r in runs), 4)

    fp_rate = rate(first_pass, terminal)
    fp_ci = wilson_ci_95(first_pass, terminal)
    rework_rt = rate(min(terminal, failed + retried + remediation_followups), terminal)
    eff_tok_succ = round(tot_tok / succeeded, 1) if succeeded > 0 else None
    eff_tok_fp = round(tot_tok / first_pass, 1) if first_pass > 0 else None
    eff_tok_pr = round(tot_tok / prs_opened, 1) if prs_opened > 0 else None
    eff_tok_merge = round(tot_tok / prs_merged_count, 1) if prs_merged_count > 0 else eff_tok_pr
    io_ratio = round(in_tok / max(out_tok, 1), 2) if tot_tok > 0 else 0.0

    flags: list[str] = []
    if terminal < MIN_CONFIDENT_SAMPLE:
        flags.append("low_sample_size")
    if terminal >= 3 and succeeded == 0 and tot_tok > 0:
        flags.append("zero_yield_token_sink")
    if _is_low_tier_model(provider, model) and terminal >= 3 and (rework_rt or 0.0) >= 0.40:
        flags.append("tier_mismatch_waste")
    if failure_breakdown.get("unverified_output", 0) + failure_breakdown.get("verification:unverified", 0) >= 2:
        flags.append("unverified_output_churn")
    if terminal >= 3 and (rework_rt or 0.0) >= 0.35:
        flags.append("high_rework_rate")
    if io_ratio > 120.0 and in_tok > 50_000:
        flags.append("excessive_input_context")

    score, grade = _score_and_grade(terminal, fp_rate, fp_ci, rework_rt, eff_tok_fp)
    return {
        "provider": provider,
        "model": model,
        "role": role,
        "complexity_tier": complexity_tier,
        "sample_size": len(runs),
        "terminal_runs": terminal,
        "neutral_runs": neutral,
        "infra_failed_runs": infra_failed,
        "availability": rate(terminal + neutral, terminal + neutral + infra_failed),
        "in_flight_runs": in_flight,
        "succeeded_runs": succeeded,
        "first_pass_succeeded_runs": first_pass,
        "failed_runs": failed,
        "retried_runs": retried,
        "remediation_followup_runs": remediation_followups,
        "success_rate": rate(succeeded, terminal),
        "first_pass_success_rate": fp_rate,
        "first_pass_ci_95": list(fp_ci) if fp_ci is not None else None,
        "rework_rate": rework_rt,
        "remediation_followups_per_pr": round(remediation_followups / max(prs_opened, 1), 3) if prs_opened else 0.0,
        "prs_opened": prs_opened,
        "prs_merged": prs_merged_count,
        "pr_yield_rate": rate(prs_opened, coding_dispatches) if coding_dispatches > 0 else None,
        "merge_rate": rate(prs_merged_count, prs_opened) if prs_opened > 0 else None,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "standards_tokens": std_tok,
        "total_tokens": tot_tok,
        "wasted_tokens": wasted_tokens,
        "wasted_tokens_pct": rate(wasted_tokens, tot_tok),
        "input_to_output_ratio": io_ratio,
        "cost_usd": cost,
        "effective_tokens_per_success": eff_tok_succ,
        "effective_tokens_per_first_pass": eff_tok_fp,
        "effective_tokens_per_pr": eff_tok_pr,
        "effective_tokens_per_merge": eff_tok_merge,
        "effective_cost_per_success_usd": round(cost / succeeded, 4) if succeeded > 0 else None,
        "infinite_cost_warning": bool(terminal >= 2 and succeeded == 0 and tot_tok > 0),
        "failure_breakdown": failure_breakdown,
        "flags": flags,
        "leaderboard_score": score,
        "grade": grade,
    }


def _build_bean_counter_ledger(
    agent_cards: Sequence[dict[str, Any]],
    role_tier_cards: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    total_tok = sum(int(c["total_tokens"]) for c in agent_cards)
    wasted_tok = sum(int(c["wasted_tokens"]) for c in agent_cards)
    std_tok = sum(int(c["standards_tokens"]) for c in agent_cards)
    total_cost = round(sum(float(c["cost_usd"]) for c in agent_cards), 4)
    actions: list[dict[str, Any]] = []
    for card in role_tier_cards:
        flags = set(card.get("flags") or ())
        prov, mod, role, tier = card["provider"], card["model"], card["role"], card["complexity_tier"]
        if "zero_yield_token_sink" in flags or "tier_mismatch_waste" in flags:
            actions.append(
                {
                    "kind": "tier_reassignment",
                    "severity": "high",
                    "provider": prov,
                    "model": mod,
                    "role": role,
                    "complexity_tier": tier,
                    "wasted_tokens": card["wasted_tokens"],
                    "first_pass_success_rate": card["first_pass_success_rate"],
                    "rework_rate": card["rework_rate"],
                    "recommendation": (
                        f"Stop routing {role} ({tier}) to {prov}/{mod} "
                        f"(first-pass yield {card['first_pass_success_rate']}, "
                        f"wasted {card['wasted_tokens']:,} tokens); route to higher-tier claude or codex."
                    ),
                }
            )
        elif "excessive_input_context" in flags:
            actions.append(
                {
                    "kind": "context_diet",
                    "severity": "medium",
                    "provider": prov,
                    "model": mod,
                    "role": role,
                    "complexity_tier": tier,
                    "wasted_tokens": card["wasted_tokens"],
                    "input_to_output_ratio": card["input_to_output_ratio"],
                    "recommendation": (
                        f"Reduce input context bloat on {role} ({prov}/{mod}, "
                        f"in/out ratio {card['input_to_output_ratio']}x): use Silent Cartographer / Alexandria "
                        f"budgeted passages instead of whole-file reads."
                    ),
                }
            )
    actions.sort(key=lambda a: (0 if a["severity"] == "high" else 1, -int(a.get("wasted_tokens") or 0)))
    return {
        "total_tokens": total_tok,
        "productive_tokens": max(0, total_tok - wasted_tok),
        "wasted_tokens": wasted_tok,
        "wasted_tokens_pct": rate(wasted_tok, total_tok),
        "standards_tokens": std_tok,
        "standards_tokens_share": rate(std_tok, total_tok),
        "total_cost_usd": total_cost,
        "economization_actions": actions,
    }


def _build_routing_recommendations(
    role_tier_cards: Sequence[dict[str, Any]],
    available: Sequence[str] = ("claude", "codex", "antigravity"),
) -> list[dict[str, Any]]:
    avail_set = {p.strip().lower() for p in available if p}
    by_bucket: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for card in role_tier_cards:
        by_bucket.setdefault((card["role"], card["complexity_tier"]), []).append(card)

    recommendations: list[dict[str, Any]] = []
    for (role, tier), cards in sorted(by_bucket.items()):
        avoid = [
            f"{c['provider']}/{c['model']}"
            for c in cards
            if "zero_yield_token_sink" in c["flags"] or "tier_mismatch_waste" in c["flags"]
        ]
        viable = [
            c
            for c in cards
            if c["provider"] in avail_set
            and f"{c['provider']}/{c['model']}" not in avoid
            and (c["first_pass_success_rate"] or 0.0) > 0.0
        ]
        viable.sort(key=lambda c: (-float(c["leaderboard_score"]), float(c["effective_tokens_per_first_pass"] or 1e12)))
        if viable:
            best = viable[0]
            fallback = (
                viable[1]["provider"] if len(viable) > 1 else ("codex" if best["provider"] != "codex" else "claude")
            )
            conf = "high" if best["terminal_runs"] >= MIN_CONFIDENT_SAMPLE else "low_sample"
            rationale = (
                f"{best['provider']}/{best['model']} leads on {role} ({tier}) with "
                f"score {best['leaderboard_score']} (first-pass {best['first_pass_success_rate']}, "
                f"N={best['terminal_runs']}, effective tokens/success {best['effective_tokens_per_success']})."
            )
            rec_prov, rec_mod = best["provider"], best["model"]
        else:
            rec_prov = "claude" if "claude" in avail_set else next(iter(avail_set), "claude")
            rec_mod, fallback, conf = "default", ("codex" if rec_prov != "codex" else "claude"), "heuristic_fallback"
            rationale = f"Insufficient passing empirical runs for {role} ({tier}); defaulting to {rec_prov}."

        recommendations.append(
            {
                "role": role,
                "complexity_tier": tier,
                "recommended_provider": rec_prov,
                "recommended_model": rec_mod,
                "fallback_provider": fallback,
                "confidence": conf,
                "avoid_models": avoid,
                "rationale": rationale,
            }
        )
    return recommendations


def build_report_cards(
    runs: Sequence[RunRecord],
    facts: Mapping[PrKey, PrFact] | None = None,
    *,
    available_providers: Sequence[str] = ("claude", "codex", "antigravity"),
    role_filter: str | None = None,
    provider_filter: str | None = None,
) -> dict[str, Any]:
    """Compute zero-LLM-cost Agent Report Cards, Bean-Counter Ledger & Leaderboard."""
    pr_facts: Mapping[PrKey, PrFact] = facts or {}
    pr_targets, branch_targets = _remediated_targets(runs)
    filtered = [
        r
        for r in runs
        if (not role_filter or r.role == role_filter) and (not provider_filter or r.provider == provider_filter)
    ]

    by_agent: dict[tuple[str, str], list[RunRecord]] = {}
    by_role_tier: dict[tuple[str, str, str, str], list[RunRecord]] = {}
    for r in filtered:
        mod, tier = _norm_model(r), infer_complexity_tier(r)
        by_agent.setdefault((r.provider, mod), []).append(r)
        by_role_tier.setdefault((r.provider, mod, r.role, tier), []).append(r)

    agent_cards = [
        _build_card(group, pr_targets, branch_targets, pr_facts, provider=prov, model=mod)
        for (prov, mod), group in by_agent.items()
    ]
    agent_cards.sort(
        key=lambda c: (
            -float(c["leaderboard_score"]),
            -int(c["first_pass_succeeded_runs"]),
            float(c["effective_tokens_per_success"] or 1e12),
        )
    )
    for idx, card in enumerate(agent_cards, start=1):
        card["rank"] = idx

    role_tier_cards = [
        _build_card(
            group, pr_targets, branch_targets, pr_facts, provider=prov, model=mod, role=role, complexity_tier=tier
        )
        for (prov, mod, role, tier), group in by_role_tier.items()
    ]
    role_tier_cards.sort(key=lambda c: (c["role"], c["complexity_tier"], -float(c["leaderboard_score"])))

    return {
        "total_runs_analyzed": len(filtered),
        "methodology": {
            "zero_llm_cost": True,
            "confidence_interval": "wilson_95",
            "min_confident_sample": MIN_CONFIDENT_SAMPLE,
            "attribution": "SQLite runs + PR remediation follow-up linkage (panel th_5e95506efbf0)",
        },
        "leaderboard": agent_cards,
        "role_tier_breakdown": role_tier_cards,
        "routing_recommendations": _build_routing_recommendations(role_tier_cards, available=available_providers),
        "bean_counter_ledger": _build_bean_counter_ledger(agent_cards, role_tier_cards),
    }
