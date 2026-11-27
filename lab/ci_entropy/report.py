# ruff: noqa: T201
"""Answer "is CI deterministic enough to trust auto-merge?" from a fixture.

Reads a JSON fixture of an *ordered* stream of CI run conclusions and applies
the primitives in :mod:`lab.ci_entropy.entropy` to it:

- **marginal entropy** of the conclusion mix,
- a **Markov transition matrix** over consecutive conclusions,
- the **entropy rate** (surprise about the next run even knowing the last),
- **memory** — how much of the marginal entropy is explained by the previous
  run — and
- a per-day entropy curve to eyeball **drift**.

Usage::

    python -m lab.ci_entropy.report [fixture.json]
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from lab.ci_entropy.entropy import (
    entropy_rate,
    shannon_entropy,
    stationary_distribution,
    transition_matrix,
)

# Illustrative, NOT calibrated thresholds (see README "Honest limits").
_DETERMINISTIC_BITS = 0.5
_COIN_FLIP_BITS = 1.2


def load_fixture(
    path: str | Path,
) -> tuple[dict, list[str], list[dict[str, int]]]:
    """Load and cross-check a fixture; returns (meta, sequence, daily_counts)."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    meta = doc["meta"]
    sequence = doc["sequence"]
    daily_counts = doc["daily_counts"]
    states = meta["states"]

    if not sequence:
        raise ValueError("fixture sequence is empty")

    expected = meta.get("window_days", len(daily_counts)) * meta.get("runs_per_day", 0)
    if expected and expected != len(sequence):
        raise ValueError(f"meta window ({expected} runs) does not match sequence length ({len(sequence)})")

    # The per-day counts must be a partition of the full sequence.
    flattened = {state: 0 for state in states}
    for day in daily_counts:
        for state, count in day.items():
            flattened[state] = flattened.get(state, 0) + count
    actual = {state: sequence.count(state) for state in states}
    if flattened != actual:
        raise ValueError("daily_counts do not partition the sequence")

    return meta, sequence, daily_counts


def per_day_entropy(daily_counts: Sequence[dict[str, int]], states: Sequence[str]) -> list[float]:
    """Shannon entropy (bits) of each day's conclusion mix."""
    return [shannon_entropy([day.get(state, 0) for state in states]) for day in daily_counts]


def render_curve(values: Sequence[float], width: int = 40) -> str:
    """ASCII bar chart of per-day entropy, scaled to the observed max."""
    peak = max(values) or 1.0
    lines = []
    for day, value in enumerate(values):
        bar = "#" * max(1, round(width * value / peak))
        lines.append(f"  day {day:>2}  {bar:<{width}} {value:.3f} bits")
    return "\n".join(lines)


def drift(per_day: Sequence[float]) -> tuple[float, float, float]:
    """(first-half mean, second-half mean, delta) of per-day entropy, in bits."""
    half = len(per_day) // 2
    first = sum(per_day[:half]) / half
    second = sum(per_day[half:]) / (len(per_day) - half)
    return first, second, second - first


def verdict(rate: float) -> str:
    """Map an entropy rate (bits/run) onto an illustrative auto-merge stance."""
    if rate <= _DETERMINISTIC_BITS:
        return "highly deterministic - auto-merge is low-surprise"
    if rate <= _COIN_FLIP_BITS:
        return "moderately predictable - auto-merge carries real surprise"
    return "near coin-flip - auto-merge is gambling"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lab.ci_entropy.report",
        description="Information-theoretic look at CI determinism.",
    )
    parser.add_argument(
        "fixture",
        nargs="?",
        help="path to a conclusion-stream fixture (default: packaged synthetic data)",
    )
    args = parser.parse_args(argv)

    path = args.fixture or str(Path(__file__).resolve().parent / "data" / "ci_conclusions.json")
    meta, sequence, daily_counts = load_fixture(path)
    states = meta["states"]

    counts = [sequence.count(state) for state in states]
    marginal = shannon_entropy(counts)
    _, matrix = transition_matrix(sequence)
    stationary = stationary_distribution(matrix)
    rate = entropy_rate(matrix, stationary)
    memory = marginal - rate
    per_day = per_day_entropy(daily_counts, states)
    first_half, second_half, delta = drift(per_day)

    print(f"CI determinism report - {Path(path).name}")
    print("=" * 60)
    print(f"runs analysed : {len(sequence)}")
    print(f"states        : {', '.join(states)}")
    print()
    print(f"marginal entropy      : {marginal:.3f} bits/run  (mix of conclusions)")
    print(f"entropy rate          : {rate:.3f} bits/run  (surprise knowing last run)")
    print(f"memory (redundancy)   : {memory:.3f} bits      (explained by previous run)")
    print()
    print("per-day entropy (drift):")
    print(render_curve(per_day))
    print()
    sign = "+" if delta >= 0 else ""
    print(f"drift                  : {first_half:.3f} -> {second_half:.3f} bits  (delta {sign}{delta:.3f} bits)")
    print()
    print("verdict               :", verdict(rate))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
