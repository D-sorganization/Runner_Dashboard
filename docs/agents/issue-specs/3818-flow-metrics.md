# #3818 — Per-repo flow metrics (PD-4 slice A)

## Problem

Operators lack a single view of fleet repo health: how old open PRs are, how long merges take, and whether the issue backlog is shrinking week over week.

## Acceptance

1. **API** — Authenticated endpoint returns per-repo (or fleet-aggregated) metrics:
   - median/p90 **open PR age** (hours since created, open PRs only);
   - **cycle time** (created → merged/closed) for PRs merged in the last N days;
   - **weekly net burn-down** (issues closed minus opened, rolling 7-day windows).
2. **UI** — Fleet Command (or existing fleet panel) shows the same three metrics with repo filter; empty repos render zero/— without error.
3. **Tests** — Unit tests for metric computation with fixture GitHub/issue inventory data (no live API).
4. **Non-goals** — No change to merge queue rules; 72h enforcement is #3819.

## Open PR

- [#3828](https://github.com/D-sorganization/Runner_Dashboard/pull/3828) (API)
- [#3836](https://github.com/D-sorganization/Runner_Dashboard/pull/3836) (UI)
