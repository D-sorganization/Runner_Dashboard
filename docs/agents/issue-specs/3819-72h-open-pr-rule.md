# #3819 — 72-hour open PR rule (PD-4 slice B)

## Problem

Lanes can accumulate stale PRs; dispatch should nudge iterate-or-close before new claims.

## Acceptance

1. **Inventory/API** — Issues and PR inventory expose `open_pr_age_hours` and a boolean `open_pr_over_72h` (or equivalent flag) for linked lane PRs.
2. **Claim gate** — When an agent lane already has an open PR older than 72h on the same repo, `pickable`/job-board logic blocks **new** issue claims until that PR is merged, closed, or refreshed (new push resetting age policy as documented).
3. **UI** — Dashboard surfaces the flag on fleet/lane views (badge or attention row).
4. **Tests** — Regression tests for boundary at 72h and for “refresh” behavior if defined.

## Coordination

- Pairs with #3818 flow metrics panel for visibility.
- Open PR: [#3837](https://github.com/D-sorganization/Runner_Dashboard/pull/3837)
