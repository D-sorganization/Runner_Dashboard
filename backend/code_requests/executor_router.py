"""Router and provider resolution for Code Request executor stage (CR-5, #1287).

Implements tier resolution, profile pinning checks, and provider selection:
- If a Code Request pins an executor profile, use it unless the child issue
  demands a higher tier. In that case, escalate one tier and record the reason.
- Otherwise, resolve the cheapest capable, in-budget provider using the
  child's task_class and tier.
"""

from __future__ import annotations

import logging
from typing import Any

from code_requests.executor_models import ChildExecutionRecord, ExecutorTier
from code_requests.model import CodeRequest

log = logging.getLogger("dashboard.code_requests.executor_router")

TIER_ORDER: tuple[ExecutorTier, ...] = (
    ExecutorTier.OLLAMA,
    ExecutorTier.CLI,
    ExecutorTier.STRONG,
)

TIER_ESCALATION: dict[ExecutorTier, ExecutorTier] = {
    ExecutorTier.OLLAMA: ExecutorTier.CLI,
    ExecutorTier.CLI: ExecutorTier.STRONG,
}

TASK_CLASS_TO_TIER: dict[str, ExecutorTier] = {
    "format": ExecutorTier.OLLAMA,
    "lint": ExecutorTier.OLLAMA,
    "lint_fix": ExecutorTier.OLLAMA,
    "label": ExecutorTier.OLLAMA,
    "comment": ExecutorTier.OLLAMA,
    "docs": ExecutorTier.OLLAMA,
    "doc_typo": ExecutorTier.OLLAMA,
    "ci": ExecutorTier.CLI,
    "ci_fix": ExecutorTier.CLI,
    "test": ExecutorTier.CLI,
    "test_fix": ExecutorTier.CLI,
    "bug": ExecutorTier.CLI,
    "feature": ExecutorTier.CLI,
    "plan": ExecutorTier.STRONG,
    "refactor": ExecutorTier.STRONG,
    "design": ExecutorTier.STRONG,
    "security": ExecutorTier.STRONG,
}

DEFAULT_PROVIDERS_BY_TIER: dict[ExecutorTier, list[str]] = {
    ExecutorTier.OLLAMA: ["ollama", "local"],
    ExecutorTier.CLI: ["codex_cli", "claude_code_cli", "jules_cli", "cline"],
    ExecutorTier.STRONG: ["antigravity", "claude_code_cli", "maxwell"],
}

ESTIMATED_COST_PER_DISPATCH: dict[str, float] = {
    "ollama": 0.0,
    "local": 0.0,
    "codex_cli": 0.05,
    "jules_cli": 0.05,
    "cline": 0.08,
    "claude_code_cli": 0.20,
    "antigravity": 0.25,
    "maxwell": 0.30,
}


def normalize_tier(value: str | ExecutorTier) -> ExecutorTier:
    """Normalize a tier string (e.g. 'tier:cli', 'cli', 'strong') to ExecutorTier."""
    if isinstance(value, ExecutorTier):
        return value
    cleaned = str(value).strip().lower()
    if cleaned.startswith("tier:"):
        cleaned = cleaned[5:].strip()
    for tier in ExecutorTier:
        if cleaned == tier.value:
            return tier
    if "ollama" in cleaned or "local" in cleaned:
        return ExecutorTier.OLLAMA
    if "strong" in cleaned or "opus" in cleaned:
        return ExecutorTier.STRONG
    return ExecutorTier.CLI


def escalate_tier(current_tier: ExecutorTier) -> ExecutorTier | None:
    """Return the next-higher tier, or None if already at strong tier."""
    return TIER_ESCALATION.get(current_tier)


def tier_rank(tier: ExecutorTier) -> int:
    """Return numeric rank (0=ollama, 1=cli, 2=strong) for comparison."""
    return TIER_ORDER.index(tier)


def resolve_child_executor(
    child: ChildExecutionRecord,
    request: CodeRequest | None = None,
    provider_registry: dict[str, Any] | None = None,
    remaining_budget_usd: float | None = None,
) -> tuple[str, ExecutorTier, str]:
    """Resolve the executor provider and tier for a child issue.

    Returns:
        (provider_id, resolved_tier, reason)

    Invariants:
    - Precondition: child must have valid tier and task_class.
    - Postcondition: returned provider is non-empty, resolved_tier >= child.tier
      unless capped by available providers.
    """
    child_required_tier = child.tier
    pinned_profile = getattr(request, "executor_profile_id", None) if request else None

    # 1. Pinned executor profile check
    if pinned_profile:
        # Determine profile tier: infer from profile ID or registry
        profile_tier = ExecutorTier.CLI
        if "ollama" in pinned_profile or "local" in pinned_profile:
            profile_tier = ExecutorTier.OLLAMA
        elif "strong" in pinned_profile or "opus" in pinned_profile or "antigravity" in pinned_profile:
            profile_tier = ExecutorTier.STRONG

        if tier_rank(child_required_tier) > tier_rank(profile_tier):
            # Escalate one tier above pinned profile and record reason
            escalated = escalate_tier(profile_tier) or profile_tier
            reason = (
                f"escalation: child tier '{child_required_tier.value}' demands higher tier than "
                f"pinned profile '{pinned_profile}' ({profile_tier.value}); escalated to '{escalated.value}'"
            )
            provider = DEFAULT_PROVIDERS_BY_TIER.get(escalated, ["antigravity"])[0]
            log.info("Child %s pinned profile escalated: %s", child.key, reason)
            return provider, escalated, reason

        return pinned_profile, profile_tier, f"pinned_profile: using pinned executor profile '{pinned_profile}'"

    # 2. Conductor / routing fallback: find cheapest capable in-budget provider
    target_tier = child_required_tier
    candidate_providers = DEFAULT_PROVIDERS_BY_TIER.get(target_tier, ["codex_cli"])

    # If provider registry is provided, filter for available and capable providers
    if provider_registry:
        filtered: list[str] = []
        for prov_id in candidate_providers:
            info = provider_registry.get(prov_id)
            if info is not None:
                # check availability if present
                if getattr(info, "available", True):
                    filtered.append(prov_id)
            else:
                filtered.append(prov_id)
        if filtered:
            candidate_providers = filtered

    # Sort candidates by cost (cheapest first)
    sorted_candidates = sorted(
        candidate_providers,
        key=lambda p: ESTIMATED_COST_PER_DISPATCH.get(p, 0.10),
    )

    # Budget check
    if remaining_budget_usd is not None:
        affordable = [p for p in sorted_candidates if ESTIMATED_COST_PER_DISPATCH.get(p, 0.0) <= remaining_budget_usd]
        if affordable:
            sorted_candidates = affordable

    selected = sorted_candidates[0] if sorted_candidates else "codex_cli"
    reason = f"conductor_routing: selected cheapest provider '{selected}' for tier '{target_tier.value}'"
    return selected, target_tier, reason
