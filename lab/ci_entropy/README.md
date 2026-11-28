# lab/ci-entropy — is CI deterministic enough to trust auto-merge?

A mad-scientist lab experiment (draft-only, never merged). It fuses **information
theory × CI telemetry**: treat the stream of CI run conclusions
(`pass` / `fail` / `pending`) as a random source and ask how much surprise it
carries, in bits.

## The question

Auto-merge trusts CI: a green run is taken as evidence the branch is safe.
That trust is only as strong as CI's *predictability*. If conclusions are a
coin flip, auto-merge is gambling; if they are a near-deterministic function of
the previous run, they carry information you can actually act on.

So: **how many bits of surprise does a run conclusion carry, and how much of it
survives once you know the previous run?**

## How to run

```bash
# from the repo root
python -m lab.ci_entropy.report                 # uses the packaged fixture
python -m pytest lab/ci_entropy/test_entropy.py -q
```

## What's here

- `entropy.py` — stdlib-only primitives: Shannon entropy, KL divergence,
  empirical Markov transition matrices, stationary distribution (power
  iteration), and the entropy rate `H(X_{t+1} | X_t)`.
- `report.py` — a small CLI that loads a fixture and prints the marginal
  entropy, entropy rate, memory (redundancy), and a per-day entropy curve.
- `test_entropy.py` — 19 invariant tests written before the implementation
  (TDD): uniform → `log2(n)`, single outcome → `0`, deterministic chain → `0`,
  Gibbs' inequality `D(p‖q) ≥ 0`, row-stochastic matrices, fixed-point
  stationary distributions.
- `data/ci_conclusions.json` — a **synthetic** 400-run conclusion stream
  (seeded, reproducible), *not* real telemetry.

## Result on the fixture

| metric | value | meaning |
| --- | --- | --- |
| marginal entropy | 0.994 bits/run | the conclusion mix is fairly concentrated |
| entropy rate | 0.692 bits/run | knowing the last run removes ~30% of the surprise |
| memory | 0.301 bits | runs are not independent — a small Markov signal exists |
| drift | +0.202 bits | entropy rises across the window (second half noisier) |

Verdict: **moderately predictable** — auto-merge carries real surprise, and the
remaining ~0.69 bits/run is not free information.

## What it would take to be real

- **Real telemetry**: swap the synthetic fixture for an export of ordered run
  conclusions from Runner_Dashboard, over a longer window.
- **Estimate the rate, don't count it**: maximum-likelihood transition
  matrices overfit short windows; a small count-smoothed estimator plus a
  confidence interval on the entropy rate would make "drift" meaningful.
- **Per-job, not per-run**: aggregate conclusions across a single job's
  history, and separate flaky jobs from stable ones — the entropy rate is
  diagnostic per job, not globally.
- **Calibrate the thresholds**: the 0.5 / 1.2 bits "verdict" cutoffs are
  illustrative constants, not fitted to any failure data.

## Honest limits

- The fixture is **synthetic**, so the headline numbers describe a toy source,
  not production CI.
- A first-order Markov chain ignores longer-range structure (time of day,
  queue depth, a flaky runner that lasts for hours); higher-order models would
  be needed before drawing conclusions about *causes*.
- Entropy measures **predictability, not safety**: a perfectly predictable
  stream that is 100% red would score 0 bits and still be useless for merging.

## Cost & scope

~250 lines of stdlib-only Python, one small JSON fixture, no new dependencies,
no changes outside `lab/ci_entropy/`. Draft-only; the owner promotes if it is
ever worth real data.
