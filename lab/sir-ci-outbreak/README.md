# Lab experiment: SIR epidemic model vs. red CI runs

**Status:** open, draft PR, `do-not-merge`. See the notebook entry in
`Repository_Management` for the full write-up. Nothing here is ever merged;
see [docs/fleet-mad-scientist.md](https://github.com/D-sorganization/Repository_Management/blob/main/docs/fleet-mad-scientist.md).

## Can we forecast "outbreaks" of red CI with an SIR epidemic model?

Fields fused: epidemiology (SIR compartmental models) + CI operations.

## What was built

- `data/agent_fleet_dashboard_runs.json`: a recorded snapshot of the last
  ~300 runs of the `Agent Fleet Dashboard` scheduled workflow on
  `D-sorganization/Runner_Dashboard`, fetched with a read-only
  `gh api repos/.../actions/workflows/265547139/runs` GET. No live writes,
  no new accounts, no secrets.
- `sir_model.py` (stdlib only, no new dependencies): bins the runs into 2-hour
  windows, computes the fraction of "red" (`cancelled`/`failure`) runs per
  bin, fits a discrete SIR model to that red-fraction curve by grid search,
  and compares the fit against the same model fit to a temporally shuffled
  version of the same data (same base rate, no clustering). It also reports
  the lag-1 autocorrelation of the red-fraction series.

Run it with:

```bash
python3 lab/sir-ci-outbreak/sir_model.py
```

It prints its reasoning and (re)writes `report.md` next to it.

## What happened

See `report.md` for the numbers. Short version: the real series starts at
`I=0`, which traps SIR's `beta*S*I` term at zero, so the naive best fit is a
degenerate flat line — a category mismatch, not a tuning problem, since SIR
assumes infections grow by contact once seeded, while CI red runs are
reintroduced by independent external shocks. Breaking that zero-trap with a
small ambient floor gives a real-vs-shuffled SSE ratio of ~0.73 and a lag-1
autocorrelation of ~0.42: red runs do cluster for a bin or two, but not
enough, and not in the right *shape*, for a contact-based epidemic model to
be the right tool. This matches the fleet's existing lore (recorded in the
role's worked example) that CI failures arrive in bursts after shocks, not
via contagion.

## What it would take

A real version would need a much larger, continuously updated snapshot (this
one is ~300 runs over 7 days of one workflow) and a model shaped like the
actual mechanism — e.g. a Hawkes (self-exciting point process) model, which
allows bursts to be triggered by external events and lets each red run
slightly raise near-term red probability without requiring epidemic-style
growth from a single seed. That is a bigger lift than this run's budget; it's
written up here as "what it would take," not built.
