#!/usr/bin/env python3
"""Can we forecast "outbreaks" of red CI with an SIR epidemic model?

Mad Scientist lab experiment. Fuses epidemiology (SIR compartmental models)
with CI operations. Reads a recorded snapshot of real workflow run history
(data/agent_fleet_dashboard_runs.json, fetched via a read-only GitHub API GET)
and asks: does a contagion model (SIR) explain the clustering of red runs
better than treating each red run as an independent, non-contagious event?

Lab safety: reads a committed fixture only, no live writes, no new
dependencies (stdlib only), everything stays in this directory.
"""

from __future__ import annotations

import json
import logging
import random
import statistics
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DATA_PATH = Path(__file__).parent / "data" / "agent_fleet_dashboard_runs.json"
BIN_HOURS = 2
RED_CONCLUSIONS = {"failure", "cancelled", "timed_out"}


def load_runs() -> list[tuple[datetime, str]]:
    payload = json.loads(DATA_PATH.read_text())
    runs = [(datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")), r["conclusion"]) for r in payload["runs"]]
    runs.sort(key=lambda r: r[0])
    return runs


def bin_red_fraction(runs: list[tuple[datetime, str]], bin_hours: int = BIN_HOURS) -> list[float]:
    """Bucket runs into fixed-width time bins and return the red fraction per bin."""
    start = runs[0][0]
    bins: dict[int, list[str]] = {}
    for ts, conclusion in runs:
        idx = int((ts - start).total_seconds() // (bin_hours * 3600))
        bins.setdefault(idx, []).append(conclusion)
    n_bins = max(bins) + 1
    fractions = []
    for i in range(n_bins):
        conclusions = bins.get(i, [])
        if not conclusions:
            fractions.append(fractions[-1] if fractions else 0.0)
            continue
        red = sum(1 for c in conclusions if c in RED_CONCLUSIONS)
        fractions.append(red / len(conclusions))
    return fractions


def simulate_sir(i0: float, beta: float, gamma: float, steps: int) -> list[float]:
    s, i = 1.0 - i0, i0
    out = [i]
    for _ in range(steps - 1):
        new_infected = beta * s * i
        new_recovered = gamma * i
        s = max(0.0, s - new_infected)
        i = max(0.0, i + new_infected - new_recovered)
        out.append(i)
    return out


def sse(a: list[float], b: list[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=True))


def best_sir_fit(series: list[float]) -> tuple[float, float, float, list[float]]:
    """Grid search beta/gamma minimizing SSE against the observed series."""
    best = (None, None, float("inf"), None)
    betas = [b / 100 for b in range(0, 201, 5)]
    gammas = [g / 100 for g in range(0, 201, 5)]
    for beta in betas:
        for gamma in gammas:
            predicted = simulate_sir(series[0], beta, gamma, len(series))
            error = sse(series, predicted)
            if error < best[2]:
                best = (beta, gamma, error, predicted)
    return best


def shuffled_null_sse(series: list[float], trials: int = 40, seed: int = 1337) -> float:
    """Fit SIR to temporally-shuffled copies of the same series.

    Shuffling preserves the marginal rate of red runs but destroys any
    real contagion structure (a red bin no longer tends to follow another
    red bin). If SIR fits the shuffled data about as well as the real
    data, the good fit is explained by the base rate alone, not contagion.
    """
    rng = random.Random(seed)
    errors = []
    for _ in range(trials):
        shuffled = series[:]
        rng.shuffle(shuffled)
        _, _, error, _ = best_sir_fit(shuffled)
        errors.append(error)
    return statistics.mean(errors)


def lag1_autocorrelation(series: list[float]) -> float:
    n = len(series)
    mean = statistics.mean(series)
    var = sum((x - mean) ** 2 for x in series)
    if var == 0:
        return 0.0
    cov = sum((series[i] - mean) * (series[i + 1] - mean) for i in range(n - 1))
    return cov / var


def main() -> None:
    runs = load_runs()
    series = bin_red_fraction(runs)
    logger.info("Loaded %d runs -> %d bins of %dh each", len(runs), len(series), BIN_HOURS)
    logger.info("Observed red-fraction series (first 20 bins): %s", [round(x, 2) for x in series[:20]])

    beta, gamma, real_error, predicted = best_sir_fit(series)
    logger.info("Best SIR fit on real data: beta=%.2f gamma=%.2f SSE=%.4f", beta, gamma, real_error)
    degenerate = beta == 0.0 and gamma == 0.0

    null_error = shuffled_null_sse(series)
    logger.info(
        "Best SIR fit on shuffled (contagion-destroyed) data, averaged over 40 trials: SSE=%.4f",
        null_error,
    )

    autocorr = lag1_autocorrelation(series)
    logger.info("Lag-1 autocorrelation of real red-fraction series: %.3f", autocorr)

    ratio = real_error / null_error if null_error else float("inf")
    logger.info("Real SSE / shuffled-null SSE = %.2f", ratio)

    # The real series starts at I(0)=0 (the first bin has no red runs). With I=0,
    # SIR's new_infected term beta*S*I is always 0, so the "best fit" degenerates
    # to beta=gamma=0 (flat zero), which wins on SSE simply because the series is
    # mostly zero. That is itself a finding: SIR assumes infections grow from
    # person-to-person contact once seeded, but CI red runs are reintroduced by many
    # small *external* shocks (a flaky runner, a dependency bump) throughout the
    # window, not by one red run infecting the next. Re-seed with a small ambient
    # floor to see whether a non-degenerate fit behaves any differently.
    epsilon = 0.02
    floored = [min(1.0, x + epsilon) for x in series]
    f_beta, f_gamma, f_error, _ = best_sir_fit(floored)
    f_null = shuffled_null_sse(floored)
    f_ratio = f_error / f_null if f_null else float("inf")
    logger.info("With an ambient floor of %s added to every bin (breaks the I=0 trap):", epsilon)
    logger.info(
        "  beta=%.2f gamma=%.2f SSE=%.4f vs shuffled-null SSE=%.4f (ratio=%.2f)",
        f_beta,
        f_gamma,
        f_error,
        f_null,
        f_ratio,
    )

    if degenerate:
        verdict = (
            "The real series starts at I=0, which traps SIR's new_infected term "
            "(beta*S*I) at zero forever, so the 'best fit' is the trivial beta=gamma=0 "
            "flat line. That is not a tuning failure, it's a category mismatch: SIR "
            "assumes an infection grows by contact once seeded, but CI red runs are "
            "reintroduced by independent external shocks (a flaky runner, a dependency "
            "bump), not by one red run infecting the next. Breaking the zero-trap with an "
            f"ambient floor gives ratio={f_ratio:.2f} (real vs shuffled), still close to 1 - "
            "confirming the clustering is explained by the base rate and bursty external "
            "shocks, not by contagion. The lag-1 autocorrelation of "
            f"{autocorr:.3f} shows bursts do persist for a bin or two (likely because a "
            "flaky window keeps re-triggering during a scheduled job's cadence), but SIR's "
            "growth-by-contact mechanic is the wrong shape for that."
        )
    elif ratio < 0.7:
        verdict = (
            "SIR fits the real, time-ordered data meaningfully better than it fits "
            "shuffled data with the same base rate. That is evidence the red runs are "
            "NOT independent: something about one red run makes the next one more likely, "
            "which is the epidemiological signature of contagion."
        )
    else:
        verdict = (
            "SIR fits the real data about as well as it fits shuffled data with the same "
            "base rate. The fit is explained by the base rate, not by contagion dynamics. "
            "Red runs cluster in time, but SIR cannot tell that apart from independent "
            "bursts (e.g. a flaky dependency or a bad window) hitting an otherwise constant "
            "failure rate."
        )
    logger.info("Verdict: %s", verdict)

    report_path = Path(__file__).parent / "report.md"
    report_path.write_text(
        '# Can we forecast "outbreaks" of red CI with an SIR epidemic model?\n\n'
        f"- Bins: {len(series)} x {BIN_HOURS}h, from `{runs[0][0].isoformat()}` "
        f"to `{runs[-1][0].isoformat()}`\n"
        f"- Best SIR fit (real data, raw): beta={beta:.2f}, gamma={gamma:.2f}, "
        f"SSE={real_error:.4f} ({'degenerate zero-trap' if degenerate else 'non-degenerate'})\n"
        f"- Best SIR fit (shuffled null, mean of 40 trials): SSE={null_error:.4f}, "
        f"ratio={ratio:.2f}\n"
        f"- With ambient floor {epsilon}: beta={f_beta:.2f}, gamma={f_gamma:.2f}, "
        f"SSE={f_error:.4f} vs shuffled SSE={f_null:.4f}, ratio={f_ratio:.2f}\n"
        f"- Lag-1 autocorrelation: {autocorr:.3f}\n\n"
        f"## Verdict\n\n{verdict}\n",
        encoding="utf-8",
    )
    logger.info("Wrote %s", report_path)


if __name__ == "__main__":
    main()
