"""Fit an SIR epidemic model to a snapshot of red CI runs and report the result.

Run from the repo root (this lab worktree):

    python -m lab.ci_sir.report [--fixture lab/ci_sir/data/ci_red_runs.json]

Pure standard library: no install step, no network, no secrets.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lab.ci_sir.sir import SIRParams, basic_reproduction_number, r0_from_final_size, simulate

DEFAULT_FIXTURE = Path(__file__).resolve().parent / "data" / "ci_red_runs.json"
_STEPS_PER_DAY = 20  # dt = 0.05 day -> 20 integration steps per day


def load_fixture(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    meta = data["meta"]
    required = {"window_days", "population", "mean_recovery_days"}
    missing = required - set(meta)
    if missing:
        raise ValueError(f"fixture meta missing fields: {sorted(missing)}")
    if len(data["daily_new_red"]) != meta["window_days"]:
        raise ValueError("daily_new_red length must equal meta.window_days")
    return data


def fit_params(fixture: dict) -> tuple[SIRParams, float]:
    meta = fixture["meta"]
    daily = fixture["daily_new_red"]
    total_cases = float(sum(daily))
    population = int(meta["population"])
    if not 0 < total_cases < population:
        raise ValueError("total cases must be strictly between 0 and population")

    attack_rate = total_cases / population
    r0 = r0_from_final_size(attack_rate)
    gamma = 1.0 / float(meta["mean_recovery_days"])
    return SIRParams(beta=r0 * gamma, gamma=gamma, population=population), attack_rate


def observed_peak_day(daily: list[int]) -> int:
    return max(range(len(daily)), key=daily.__getitem__)


def render_curve(title: str, values: list[float], width: int = 46) -> str:
    top = max(values) or 1.0
    lines = [title]
    for day, value in enumerate(values):
        bar = "#" * max(0, round(value / top * width))
        lines.append(f"day {day:>2} |{bar:<{width}}| {value:.0f}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE, help="snapshot JSON to read")
    args = parser.parse_args(argv)

    fixture = load_fixture(args.fixture)
    meta = fixture["meta"]
    daily = [int(x) for x in fixture["daily_new_red"]]

    params, attack_rate = fit_params(fixture)
    r0 = basic_reproduction_number(params)

    states = simulate(
        params,
        i0=float(daily[0]),
        horizon_days=float(meta["window_days"]),
        dt=1.0 / _STEPS_PER_DAY,
    )
    model_infected = [states[k * _STEPS_PER_DAY].infected for k in range(len(daily))]
    model_peak = max(range(len(model_infected)), key=model_infected.__getitem__)

    print("Can we forecast an 'outbreak' of red CI with an SIR epidemic model?")
    print("=" * 78)
    print(f"Snapshot: {meta['window_days']} days, {int(sum(daily))}/{meta['population']} runs went red "
          f"(attack rate {attack_rate:.1%})")
    print(f"Fitted:   R0 = {r0:.3f}  (beta = {params.beta:.3f}/day, gamma = {params.gamma:.3f}/day)")
    print(f"          mean time-to-green = {meta['mean_recovery_days']:.1f} days")
    print()
    print(render_curve("Observed new-red runs per day (fixture):", [float(v) for v in daily]))
    print()
    print(render_curve("Model 'infected' (currently red) runs per day:", model_infected))
    print()
    print(f"Observed peak: day {observed_peak_day(daily)}   Model peak: day {model_peak}")
    verdict = "OUTBREAK (R0 > 1)" if r0 > 1 else "no outbreak (R0 <= 1)"
    print(f"Verdict: {verdict} — red CI can spread like an epidemic.")
    if r0 > 1:
        contact_cut = (r0 - 1.0) / r0
        print(f"To push R0 below 1, cut 'contact' (new failures per red run per day) by {contact_cut:.1%} "
              f"— e.g. hold merges to main.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
