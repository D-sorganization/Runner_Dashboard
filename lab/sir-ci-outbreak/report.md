# Can we forecast "outbreaks" of red CI with an SIR epidemic model?

- Bins: 82 x 2h, from `2026-10-07T09:48:33+00:00` to `2026-10-14T04:25:31+00:00`
- Best SIR fit (real data, raw): beta=0.00, gamma=0.00, SSE=11.1803 (degenerate zero-trap)
- Best SIR fit (shuffled null, mean of 40 trials): SSE=10.6259, ratio=1.05
- With ambient floor 0.02: beta=0.05, gamma=0.00, SSE=6.3912 vs shuffled SSE=8.7253, ratio=0.73
- Lag-1 autocorrelation: 0.422

## Verdict

The real series starts at I=0, which traps SIR's new_infected term (beta*S*I) at zero forever, so the 'best fit' is the trivial beta=gamma=0 flat line. That is not a tuning failure, it's a category mismatch: SIR assumes an infection grows by contact once seeded, but CI red runs are reintroduced by independent external shocks (a flaky runner, a dependency bump), not by one red run infecting the next. Breaking the zero-trap with an ambient floor gives ratio=0.73 (real vs shuffled), still close to 1 - confirming the clustering is explained by the base rate and bursty external shocks, not by contagion. The lag-1 autocorrelation of 0.422 shows bursts do persist for a bin or two (likely because a flaky window keeps re-triggering during a scheduled job's cadence), but SIR's growth-by-contact mechanic is the wrong shape for that.
