# lab/ci-sir — "Can we forecast a CI outbreak with an SIR model?"

**Status:** draft-only lab experiment. Do **not** merge, ready, or deploy.
See `Repository_Management/docs/lab/README.md` for the notebook convention.

## The question

A wave of red (failing) runs on `main` looks a lot like an epidemic: one broken
change infects the runs that depend on it, those in turn break more runs, and
eventually the wave burns out as fixes land. This experiment fuses two fields —
**epidemiology** (the SIR model) and **software engineering** (CI telemetry) — to
ask: can we fit an SIR model to red-run counts and read off an `R0`?

## How to run

```bash
python -m lab.ci_sir.report --fixture lab/ci_sir/data/ci_red_runs.json
pytest lab/ci_sir/test_sir.py -q
```

Standard library only — no install, no network, no secrets.

## What it does

1. Reads a synthetic 30-day snapshot of "runs first observed red per day"
   (`data/ci_red_runs.json`) over a population of 900 runs.
2. Fits `R0` from the observed attack rate via the closed-form inverse of the
   SIR final-size equation: `R0 = -ln(1 - z) / z`.
3. Sets `gamma = 1 / mean_recovery_days` and `beta = R0 * gamma`.
4. Simulates the mass-action SIR ODE with Euler integration and prints an ASCII
   curve of the "currently red" (infected) compartment plus a verdict.

## Findings (on the synthetic fixture)

- Total red runs = **596 / 900** → attack rate ≈ **66.2%**.
- Fitted **R0 ≈ 1.64** (> 1 → outbreak regime).
- To push R0 below 1 you'd need to cut effective contact by
  `(R0 - 1) / R0 ≈ 39%` — i.e. reduce how many new failures each red run
  spawns per day, e.g. by holding merges to `main` while it is red.

## What it would take to be real

- **Live telemetry** instead of the synthetic fixture (this snapshot is
  fabricated; the model is only as honest as its input).
- **Incidence vs prevalence:** the fixture counts *new* red runs per day
  (incidence); the SIR "infected" compartment is *currently* red (prevalence).
  Fitting incidence properly needs a model that emits new infections per step
  and a likelihood, not just a final-size point estimate.
- **Heterogeneity:** runs have different dependence graphs; mass action assumes
  uniform mixing, which CI violates. A network/next-generation-matrix model
  would be the real upgrade.

## Cost & scope

- Pure stdlib prototype, ~250 lines, no services, no secrets, no budget spent.
- Lives on a `lab/` branch; expires after 30 days unless promoted per the lab
  convention. Never merged as-is.
