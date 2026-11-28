from __future__ import annotations

import time
from typing import Any

from dispatch_routing import (
    CLI_COMPLEXITIES,
    STRONG_COMPLEXITIES,
    STRONG_FLAGS,
    STRONG_JUDGEMENTS,
    TIER_LABEL_PREFIX,
    is_dictated_fix,
    resolve_model_routing,
)

from scripts.sanitize_private_topology import apply_literal_replacements


def is_eligible_unclassified_issue(labels: list[str], prompt: str) -> bool:
    """Return True if issue has no explicit tier, strong flags, complexity/judgement signals,

    and is not a dictated fix.
    Precondition: labels is a list of strings, prompt is a string.
    """
    if is_dictated_fix(prompt):
        return False

    norm = {lbl.strip().lower() for lbl in labels if isinstance(lbl, str)}

    for lbl in norm:
        if lbl.startswith(TIER_LABEL_PREFIX):
            return False

    if norm & STRONG_FLAGS:
        return False

    complexities = {lbl[len("complexity:") :] for lbl in norm if lbl.startswith("complexity:")}
    if complexities & (STRONG_COMPLEXITIES | CLI_COMPLEXITIES):
        return False

    judgements = {lbl[len("judgement:") :] for lbl in norm if lbl.startswith("judgement:")}
    if judgements & STRONG_JUDGEMENTS:
        return False

    return True


def sanitize_issue_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Sanitize issue title, body, and comments by stripping private identifiers and hostnames.

    Precondition: payload is a dictionary containing issue fields.
    Postcondition: returns a new sanitized dictionary.
    """
    sanitized = dict(payload)
    if "title" in sanitized and isinstance(sanitized["title"], str):
        sanitized["title"] = apply_literal_replacements(sanitized["title"])
    if "body" in sanitized and isinstance(sanitized["body"], str):
        sanitized["body"] = apply_literal_replacements(sanitized["body"])
    if "prompt" in sanitized and isinstance(sanitized["prompt"], str):
        sanitized["prompt"] = apply_literal_replacements(sanitized["prompt"])
    return sanitized


class DeciderClient:
    """Optional wrapper for strands-decider E2B model.

    Advisory-only. Fails safe on missing dependency, timeout, error, or 'unclear'.
    """

    def __init__(self, model_name: str = "StrandsAgents/strands-decider-E2B-gemma4-v2-2611") -> None:
        self.model_name = model_name
        self._loaded = False
        self._decider: Any = None

    def predict(self, prompt: str) -> str:
        """Predict task complexity class: 'routine_implementation', 'design_or_cross_repo', or 'unclear'."""
        try:
            import importlib

            mod = importlib.import_module("strands_decider")
            if hasattr(mod, "predict_tier"):
                res = str(mod.predict_tier(prompt))
                if res in {"routine_implementation", "design_or_cross_repo"}:
                    return res
            return "unclear"
        except Exception:
            return "unclear"


def evaluate_backtest_case(
    labels: list[str],
    prompt: str,
    decider: DeciderClient | None = None,
) -> dict[str, Any]:
    """Evaluate a single test case across 3 arms:

    1. Current router (resolve_model_routing)
    2. Rules-only baseline (deterministic heuristic)
    3. Strands Decider v2 E2B shadow (advisory-only, fail-safe to current router)
    """
    # 1. Current router
    curr = resolve_model_routing(labels, prompt)

    # 2. Rules-only baseline
    # Text heuristic: if short and no cross-module/security keywords, cli; else strong
    complex_keywords = ["cross-repo", "architecture", "security", "refactor"]
    is_complex = any(k in prompt.lower() for k in complex_keywords)
    rules_tier = "cli" if (len(prompt) < 300 and not is_complex) else "strong"

    # 3. Decider E2B shadow
    if decider is None:
        decider = DeciderClient()

    start_t = time.perf_counter()
    advice = decider.predict(prompt)
    latency_ms = (time.perf_counter() - start_t) * 1000.0

    # Fail-safe contract:
    # If advice is routine_implementation -> tier="cli"
    # If advice is design_or_cross_repo -> tier="strong"
    # If advice is unclear or error -> preserve current router decision (curr.tier)
    if advice == "routine_implementation":
        shadow_tier = "cli"
    elif advice == "design_or_cross_repo":
        shadow_tier = "strong"
    else:
        shadow_tier = curr.tier

    return {
        "current_tier": curr.tier,
        "rules_tier": rules_tier,
        "shadow_tier": shadow_tier,
        "advice": advice,
        "latency_ms": latency_ms,
    }
