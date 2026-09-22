"""Price table and cost estimator for staff runs (issue #1200).

Two pricing mechanisms, tried in order:

1. **Token table** — USD per 1M input / output tokens per provider + model.
   Claude rows are Anthropic list prices as of 2026-06; the OpenAI and Gemini
   rows are ported from ``Maxwell_Daemon/maxwell_daemon/backends/pricing.py``
   (last refreshed 2026-04) and are flagged ``estimate=True`` because they were
   not re-verified against the vendor pages when ported.
2. **Wall time** — USD per minute for providers whose CLI reports no token
   accounting (subscription seats: antigravity, cursor-agent). Default 0 so an
   unconfigured provider never fabricates spend; set ``STAFF_WALL_USD_PER_MIN``
   (``provider=rate,provider=rate``) to charge seat time.

``estimate_cost`` never raises and never returns a negative number.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    """USD per 1,000,000 tokens. ``estimate`` marks rows not verified at port time."""

    input_usd: float
    output_usd: float
    estimate: bool = False

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return round((input_tokens * self.input_usd + output_tokens * self.output_usd) / 1_000_000, 6)


# Model keys are matched as substrings of the run's model string (longest key
# wins), so ``claude-opus-5``, ``opus-5`` and ``claude-opus-5-20260401`` all hit
# the same row. Order inside a provider does not matter.
PRICE_TABLE: dict[str, dict[str, Price]] = {
    "claude": {
        "fable-5.1": Price(10.0, 50.0),
        "fable-5-1": Price(10.0, 50.0),
        "fable-5": Price(10.0, 50.0),
        "opus-5": Price(5.0, 25.0),
        "opus-4-8": Price(5.0, 25.0),
        "opus-4-7": Price(5.0, 25.0),
        "opus-4-6": Price(5.0, 25.0),
        "sonnet-5": Price(2.0, 10.0),
        "sonnet-4-6": Price(3.0, 15.0),
        "haiku-4-5": Price(1.0, 5.0),
        "haiku-4.5": Price(1.0, 5.0),
    },
    "codex": {
        "gpt-5": Price(1.25, 10.0, estimate=True),
        "gpt-5-mini": Price(0.25, 2.0, estimate=True),
        "gpt-4o": Price(2.5, 10.0, estimate=True),
        "gpt-4o-mini": Price(0.15, 0.60, estimate=True),
        "gpt-4-turbo": Price(10.0, 30.0, estimate=True),
        "o1": Price(15.0, 60.0, estimate=True),
        "o1-mini": Price(3.0, 12.0, estimate=True),
        "o3-mini": Price(1.10, 4.40, estimate=True),
        "o3": Price(2.0, 8.0, estimate=True),
        "o4-mini": Price(1.10, 4.40, estimate=True),
    },
    "gemini": {
        "gemini-2.5-pro": Price(1.25, 10.0, estimate=True),
        "gemini-2.5-flash": Price(0.15, 0.60, estimate=True),
        "gemini-2.5-flash-lite": Price(0.075, 0.30, estimate=True),
        "gemini-2.0-flash": Price(0.10, 0.40, estimate=True),
        "gemini-1.5-pro": Price(1.25, 5.0, estimate=True),
        "gemini-1.5-flash": Price(0.075, 0.30, estimate=True),
    },
    # Local inference: any model, always free.
    "ollama": {},
}

# Default model per provider when the run recorded none (mirrors the CLIs' own
# defaults on 2026-09-22; ``estimate`` rows above still apply).
DEFAULT_MODEL: dict[str, str] = {"claude": "opus-5", "codex": "gpt-5", "gemini": "gemini-2.5-pro"}

# Providers that never carry per-token cost regardless of model.
FREE_PROVIDERS: frozenset[str] = frozenset({"ollama"})

# USD per minute of CLI wall time for providers without token accounting.
# All zero by default: seat subscriptions are paid whether or not a run happens.
WALL_TIME_USD_PER_MINUTE: dict[str, float] = {"antigravity": 0.0, "cursor-agent": 0.0, "ollama": 0.0}

METHODS = ("reported", "token_table", "wall_time", "none")


def _wall_rates() -> dict[str, float]:
    """Built-in wall-time rates overlaid with ``STAFF_WALL_USD_PER_MIN``."""
    rates = dict(WALL_TIME_USD_PER_MINUTE)
    raw = os.environ.get("STAFF_WALL_USD_PER_MIN", "")
    for part in raw.split(","):
        if "=" not in part:
            continue
        provider, _, value = part.partition("=")
        try:
            rates[provider.strip()] = max(0.0, float(value))
        except ValueError:
            continue
    return rates


def lookup_price(provider: str, model: str | None) -> Price | None:
    """Return the price row for ``provider``/``model`` or ``None`` when unknown.

    Free providers return a zero row so callers can distinguish "free" from
    "unpriced". Matching is by longest key contained in the (lower-cased)
    model string; an empty model falls back to ``DEFAULT_MODEL``.
    """
    if provider in FREE_PROVIDERS:
        return Price(0.0, 0.0)
    table = PRICE_TABLE.get(provider)
    if not table:
        return None
    name = (model or DEFAULT_MODEL.get(provider, "")).lower()
    if not name:
        return None
    hits = [key for key in table if key in name]
    if not hits:
        return None
    return table[max(hits, key=len)]


def estimate_cost(
    provider: str,
    model: str | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    wall_seconds: float = 0.0,
) -> tuple[float, str]:
    """Estimate USD for one run. Returns ``(usd, method)``.

    Pre: token counts and wall seconds are non-negative (negatives are clamped).
    Post: ``method`` is ``token_table`` when tokens were known and a price row
    exists, ``wall_time`` when only wall time is known and the provider has a
    non-zero per-minute rate, else ``none`` with ``usd == 0.0``. ``reported``
    is never produced here; it is the caller's marker for CLI-reported cost.
    """
    input_tokens = max(0, int(input_tokens or 0))
    output_tokens = max(0, int(output_tokens or 0))
    wall_seconds = max(0.0, float(wall_seconds or 0.0))
    if input_tokens or output_tokens:
        price = lookup_price(provider, model)
        if price is not None:
            return price.cost(input_tokens, output_tokens), "token_table"
    rate = _wall_rates().get(provider, 0.0)
    if wall_seconds > 0 and rate > 0:
        return round(wall_seconds / 60.0 * rate, 6), "wall_time"
    return 0.0, "none"


def price_table_rows() -> list[dict[str, object]]:
    """Flat, LoD-friendly view of the table for the API and the docs."""
    rows: list[dict[str, object]] = []
    for provider, table in sorted(PRICE_TABLE.items()):
        for key, price in sorted(table.items()):
            rows.append(
                {
                    "provider": provider,
                    "model": key,
                    "input_usd_per_1m": price.input_usd,
                    "output_usd_per_1m": price.output_usd,
                    "estimate": price.estimate,
                }
            )
    for provider, rate in sorted(_wall_rates().items()):
        rows.append({"provider": provider, "model": "*", "wall_usd_per_minute": rate, "estimate": False})
    return rows
