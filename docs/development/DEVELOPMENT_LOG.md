# Development Log — Runner_Dashboard

State table for every feature in flight in this repository. Update
entries **in place**; never append dated sections. One entry per
feature, from proposal to ship. See the `development-logs` section of
`AGENTS.md` for the binding rules and
`shared_scripts/development_log.py` for the validator.

- **Portfolio:** infra
- **WIP limit:** 4
- **Last audited:** 2026-08-28 by bootstrap

## States

`proposed` → `in_progress` → `in_review` → `shipped`, with `parked`
reachable from any live state and `abandoned` from `parked`.
`shipped` never returns to `in_progress`; open a new entry instead.

## Active

### DL-0004 · Hub Dashboard Fleet Telemetry Aggregation 1169

- **State:** in_progress
- **Owner:** agent
- **PR:** not created
- **Paths:** `backend/fleet_autoconfig.py`, `backend/machine_registry.py`, `backend/routers/fleet.py`, `backend/server.py`, `tests/test_fleet_autoconfig.py`, `tests/test_hub_fleet_aggregation.py`
- **Started:** 2026-09-06
- **Last verified:** 2026-09-06
- **Summary:** Resolve Issue #1169: Hub dashboard telemetry aggregation and stale registry validation. Ensure /api/fleet/status polls and aggregates DeskComputer, OGLaptop, and ControlTower-Runner, while filtering out retired pools and validating non-retired machines on startup.

### DL-0001 · Fix Autoscaler Oglaptop Regression 907

- **State:** parked
- **Owner:** unassigned
- **PR:** not created
- **Paths:** `.` — scope not yet narrowed; set real globs when
  this entry is reactivated.
- **Started:** 2026-08-28
- **Last verified:** 2026-08-28 (`fbb4b2b`)
- **Summary:** Seeded from local branch `fix/autoscaler-oglaptop-regression-907`, which is
  2 commit(s) ahead of the default branch with no
  development-log entry.
- **Parked:** 2026-08-28 — seeded during fleet rollout. Assign a
  governing issue and set `Paths` before moving this to a live
  state; a live entry without a real issue is orphaned by
  definition.

### DL-0002 · Fix Require All Gates Before Automerge

- **State:** parked
- **Owner:** unassigned
- **PR:** not created
- **Paths:** `.` — scope not yet narrowed; set real globs when
  this entry is reactivated.
- **Started:** 2026-08-28
- **Last verified:** 2026-08-28 (`6e4fc96`)
- **Summary:** Seeded from local branch `fix/require-all-gates-before-automerge`, which is
  2 commit(s) ahead of the default branch with no
  development-log entry.
- **Parked:** 2026-08-28 — seeded during fleet rollout. Assign a
  governing issue and set `Paths` before moving this to a live
  state; a live entry without a real issue is orphaned by
  definition.

### DL-0003 · Fix Vitest Vite Esbuild Override 1085

- **State:** parked
- **Owner:** unassigned
- **PR:** not created
- **Paths:** `.` — scope not yet narrowed; set real globs when
  this entry is reactivated.
- **Started:** 2026-08-28
- **Last verified:** 2026-08-28 (`bf1230e`)
- **Summary:** Seeded from local branch `fix/vitest-vite-esbuild-override-1085`, which is
  1 commit(s) ahead of the default branch with no
  development-log entry.
- **Parked:** 2026-08-28 — seeded during fleet rollout. Assign a
  governing issue and set `Paths` before moving this to a live
  state; a live entry without a real issue is orphaned by
  definition.

## Shipped (Last 90 Days)

Entries stay here for 90 days after merge, then move to the archive.

## Archive

Older entries live in `DEVELOPMENT_LOG_ARCHIVE_<year>.md`.
