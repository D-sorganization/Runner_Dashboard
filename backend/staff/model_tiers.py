"""Task-complexity model tiers for staff runs (#2047).

Tier tables live in ``config/staff_model_tiers.json``. Models are validated
against each provider adapter's known CLI ids (see ``allowed_models_for_provider``).
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from staff.adapters import ADAPTERS, ProviderAdapter
from staff.provider_defaults import default_model_for_provider

log = logging.getLogger("dashboard.staff.model_tiers")

TierName = Literal["routine", "standard", "complex"]
TIER_NAMES: tuple[TierName, ...] = ("routine", "standard", "complex")

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "staff_model_tiers.json"

# Claude Code CLI short names used in staff (``review.py``, ``test_staff_chat.py``).
_CLAUDE_CLI_ALIASES = frozenset({"haiku", "sonnet", "opus"})

def allowed_models_for_provider(provider_id: str, adapter: ProviderAdapter | None = None) -> frozenset[str]:
    """CLI-accepted model ids for validation (fail closed on unknown)."""
    adapter = adapter or ADAPTERS.get(provider_id)
    allowed: set[str] = set()
    if adapter is not None:
        allowed.update(adapter.models)
        if adapter.default_model:
            allowed.add(adapter.default_model)
    repo = default_model_for_provider(provider_id)
    if repo:
        allowed.add(repo)
    if provider_id == "claude":
        allowed.update(_CLAUDE_CLI_ALIASES)
    if provider_id == "codex" and repo:
        allowed.add(repo)
    return frozenset(m for m in allowed if m)


@lru_cache(maxsize=1)
def _load_tier_config() -> dict[str, Any]:
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("staff_model_tiers.json unreadable (%s)", exc)
        return {}
    return data if isinstance(data, dict) else {}


def _tier_order() -> tuple[TierName, ...]:
    raw = _load_tier_config().get("tier_order")
    if isinstance(raw, list):
        order = tuple(t for t in raw if t in TIER_NAMES)
        if order:
            return order  # type: ignore[return-value]
    return TIER_NAMES


def validate_tier_config() -> list[str]:
    """Return problems with the tier table (empty when valid)."""
    problems: list[str] = []
    table = _load_tier_config().get("models_by_tier")
    if not isinstance(table, dict):
        return ["models_by_tier must be an object"]
    for tier, providers in table.items():
        if tier not in TIER_NAMES:
            problems.append(f"unknown tier {tier!r}")
            continue
        if not isinstance(providers, dict):
            problems.append(f"models_by_tier.{tier} must be an object")
            continue
        for pid, model in providers.items():
            if pid not in ADAPTERS:
                problems.append(f"models_by_tier.{tier}.{pid} is not a staff provider")
                continue
            mid = str(model).strip()
            if not mid:
                problems.append(f"models_by_tier.{tier}.{pid} is empty")
                continue
            allowed = allowed_models_for_provider(pid)
            if mid not in allowed:
                problems.append(
                    f"models_by_tier.{tier}.{pid} model {mid!r} is not allowed "
                    f"(known: {', '.join(sorted(allowed)[:8])}…)"
                )
    return problems


def normalize_tier(value: str | None, *, default: TierName = "routine") -> TierName:
    text = (value or default).strip().lower()
    if text in TIER_NAMES:
        return text  # type: ignore[return-value]
    return default


def model_for_provider_tier(provider_id: str, tier: TierName) -> str | None:
    table = _load_tier_config().get("models_by_tier") or {}
    block = table.get(tier) if isinstance(table, dict) else None
    if not isinstance(block, dict):
        return None
    raw = block.get(provider_id)
    return str(raw).strip() if raw not in (None, "") else None


def next_tier_up(tier: TierName) -> TierName | None:
    order = _tier_order()
    try:
        idx = order.index(tier)
    except ValueError:
        return None
    if idx + 1 >= len(order):
        return None
    return order[idx + 1]


def validate_role_model(provider_id: str, model: str | None) -> list[str]:
    if not model or not str(model).strip():
        return []
    mid = str(model).strip()
    allowed = allowed_models_for_provider(provider_id)
    if mid not in allowed:
        return [f"model {mid!r} is not valid for provider {provider_id!r}"]
    return []


def validate_role_tier(tier: str | None) -> list[str]:
    if tier is None or not str(tier).strip():
        return []
    if str(tier).strip().lower() not in TIER_NAMES:
        return [f"tier {tier!r} must be one of {', '.join(TIER_NAMES)}"]
    return []


def reset_tier_config_cache() -> None:
    _load_tier_config.cache_clear()
