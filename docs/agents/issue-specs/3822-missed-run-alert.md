# #3822 — Two consecutive missed scheduled runs

## Problem

A role can miss slots silently until backlog grows; two misses in a row should page operators.

## Acceptance

1. **Counter** — Per-role state tracks consecutive scheduled slots without a successful run (skipped/blocked counts as miss).
2. **Reset** — Successful run clears counter.
3. **Alert** — On reaching 2, emit debounced alert (fleet event + staff attention); do not spam on every subsequent miss until a success resets.
4. **Tests** — Failing regressions first for increment, reset, debounce.

## Open PR

- [#3832](https://github.com/D-sorganization/Runner_Dashboard/pull/3832)
