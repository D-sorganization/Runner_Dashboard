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

### DL-#1196 · Staff Hub scheduler, run windows, holds list, per-role budgets

- **State:** in_review
- **Owner:** agent
- **Issue:** #1196
- **Branch:** `feat/1196-staff-scheduler` (base `feat/staff-hub`)
- **PR:** not created
- **Paths:** `backend/staff/schedule.py`, `backend/staff/holds.py`, `backend/staff/budget.py`, `backend/staff/scheduler.py`, `backend/staff/store.py`, `backend/routers/staff_schedule.py`, `backend/server.py`, `tests/api/test_staff_schedule.py`, `docs/staff-hub.md`, `pyproject.toml`, `requirements.txt`, `uv.lock`
- **Started:** 2026-09-22
- **Last verified:** 2026-09-22 (`4c0a1e6`)
- **Summary:** In-process scheduler that reads role YAML cron (`America/Los_Angeles`) and windows, submits one run per role per slot when no active hold matches and the daily budget allows; `GET/PUT /api/staff/holds` persisted at `staff_holds.json` in the config dir and seeded from role `holds:`; `BudgetGuard` with 75/90/100 % alerts and 6 h debounce; `GET /api/staff/schedule` gate report.
- **Next step:** Open the draft PR against `feat/staff-hub` and let CI run the full suite.

### DL-#1194 · Staff Hub core: runner, run store, /api/staff routes

- **State:** in_review
- **Owner:** agent
- **Issue:** #1194
- **Branch:** `feat/staff-hub`
- **PR:** not created
- **Paths:** `backend/staff/`, `backend/routers/staff.py`, `backend/server.py`, `backend/middleware.py`, `tests/api/test_staff_runner.py`, `tests/api/test_staff_auth_perimeter.py`, `docs/staff-hub.md`
- **Started:** 2026-09-22
- **Last verified:** 2026-09-22 (`f8a3b85`)
- **Summary:** Epic #1192 foundation: dispatch named AI staff roles (Repository_Management `staff/roles/*.yml`) as local CLI subprocesses (claude, codex, agy, gemini, cursor-agent, ollama) in isolated worktrees with the RM lease ritual, persist runs and events in node-local SQLite, stream output over SSE, dry-run plans and cancellation via `/api/staff/*`.
- **Next step:** Implement #1195 hub fan-out of `/api/staff/board` on top of this branch.

### DL-#1184 · Fleet monitor pool retarget + dangling-image prune

- **State:** in_review
- **Owner:** agent
- **Issue:** #1184
- **Branch:** `fix/monitor-controltower-runner-pool`
- **PR:** not created
- **Paths:** `deploy/fleet-health-monitor.ps1`, `deploy/runner-cleanup.sh`, `tests/deploy/test_fleet_health_monitor.py`, `tests/deploy/test_runner_cleanup_disk_guard.py`
- **Started:** 2026-09-14
- **Last verified:** 2026-09-14 (`4fee405`)
- **Summary:** Resolve Issue #1184: retarget the DeskComputer fleet monitor from the retired ControlTower-SSD pool to ControlTower-Runner, prune dangling docker images on a 6h window instead of 168h, and reap leaked ~/.rustup/tmp entries on the daily cleanup pass.
- **Next step:** Deploy the merged runner-cleanup.sh and fleet-health-monitor.ps1 to ControlTower and DeskComputer.

### DL-0007 · Adopt Mermaid C4 Architecture Map Contract 1613

- **State:** in_progress
- **Owner:** agent
- **PR:** not created
- **Paths:** `docs/architecture/C4.md`, `scripts/architecture_map_contract.py`, `tests/test_architecture_map_contract.py`, `.github/workflows/architecture-map-contract.yml`, `config/workflow_runner_routing_policy.json`
- **Started:** 2026-09-10
- **Last verified:** 2026-09-10 (`38f2b2f`)
- **Summary:** Resolve Issue #1613: Adopt maintainable Mermaid C4 architecture-map contract and automated CI verification. Establish docs/architecture/C4.md with C4Context, C4Container, Feature Map, and Architecture Change Log, validated by scripts/architecture_map_contract.py and test_architecture_map_contract.py.

### DL-0006 · Orchestrator Authentication Perimeter Hardening 1173

- **State:** in_progress
- **Owner:** agent
- **PR:** not created
- **Paths:** `backend/identity.py`, `backend/middleware.py`, `backend/orchestrator_api.py`, `tests/api/test_orchestrator_api.py`
- **Started:** 2026-09-06
- **Last verified:** 2026-09-06
- **Summary:** Resolve Issue #1173: Require authentication on orchestrator state-changing endpoints (/api/orchestrator/lease, /release, /queue control) via require_orchestrator_peer, supporting principal tokens, HUB_FLEET_TOKEN bearer matching, and loopback when DASHBOARD_LOOPBACK_AUTH=1, while rejecting unauthenticated remote callers with HTTP 401.

### DL-0005 · Runner Host Reality vs /tmp Runbook and Profile Cleanup 1159

- **State:** shipped
- **Owner:** agent
- **PR:** #1172
- **Paths:** `deploy/clean-stale-shell-profiles.sh`, `deploy/install-runner-maintenance.sh`, `docs/runbooks/runner-tmp-exhaustion.md`, `tests/deploy/test_clean_stale_shell_profiles.py`
- **Started:** 2026-09-06
- **Last verified:** 2026-09-06
- **Summary:** Resolve Issue #1159: Support standalone maintenance script deployment on hosts without a local repo checkout, document explicit per-host WSL distro names, and add automated pruning of stale cargo/env source statements from ~/.profile and ~/.bashrc.

### DL-0004 · Hub Dashboard Fleet Telemetry Aggregation 1169

- **State:** shipped
- **Owner:** agent
- **PR:** #1171
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

### DL-#1195 · Staff Hub fleet board, summary and machine targeting (#1195, #1197)

- **State:** in_review
- **Owner:** agent
- **Issue:** #1195
- **Branch:** `feat/1195-hub-board-targeting`
- **PR:** not created
- **Paths:** `backend/staff/fleet.py`, `backend/routers/staff.py`, `tests/api/test_staff_fleet.py`, `docs/staff-hub.md`
- **Started:** 2026-09-22
- **Last verified:** 2026-09-22 (`4c0a1e6`)
- **Summary:** Hub fan-out of `/api/staff/board` across peer nodes, the one-call `/api/staff/summary` brief for Barb/Orchestrator, and `machine: local|<peer>|auto` targeting that forwards dispatches to the chosen node (also closes #1197).
- **Next step:** After #1196 merges, verify `summary.holds` is populated on a node with holds.

### DL-#1194 · Staff Hub core: runner, run store, /api/staff routes

- **State:** in_review
- **Owner:** agent
- **Issue:** #1194
- **Branch:** `feat/staff-hub`
- **PR:** not created
- **Paths:** `backend/staff/`, `backend/routers/staff.py`, `backend/server.py`, `backend/middleware.py`, `tests/api/test_staff_runner.py`, `tests/api/test_staff_auth_perimeter.py`, `docs/staff-hub.md`
- **Started:** 2026-09-22
- **Last verified:** 2026-09-22 (`f8a3b85`)
- **Summary:** Epic #1192 foundation: dispatch named AI staff roles (Repository_Management `staff/roles/*.yml`) as local CLI subprocesses (claude, codex, agy, gemini, cursor-agent, ollama) in isolated worktrees with the RM lease ritual, persist runs and events in node-local SQLite, stream output over SSE, dry-run plans and cancellation via `/api/staff/*`.
- **Next step:** Implement #1195 hub fan-out of `/api/staff/board` on top of this branch.

### DL-#1184 · Fleet monitor pool retarget + dangling-image prune

- **State:** in_review
- **Owner:** agent
- **Issue:** #1184
- **Branch:** `fix/monitor-controltower-runner-pool`
- **PR:** not created
- **Paths:** `deploy/fleet-health-monitor.ps1`, `deploy/runner-cleanup.sh`, `tests/deploy/test_fleet_health_monitor.py`, `tests/deploy/test_runner_cleanup_disk_guard.py`
- **Started:** 2026-09-14
- **Last verified:** 2026-09-14 (`4fee405`)
- **Summary:** Resolve Issue #1184: retarget the DeskComputer fleet monitor from the retired ControlTower-SSD pool to ControlTower-Runner, prune dangling docker images on a 6h window instead of 168h, and reap leaked ~/.rustup/tmp entries on the daily cleanup pass.
- **Next step:** Deploy the merged runner-cleanup.sh and fleet-health-monitor.ps1 to ControlTower and DeskComputer.

### DL-0007 · Adopt Mermaid C4 Architecture Map Contract 1613

- **State:** in_progress
- **Owner:** agent
- **PR:** not created
- **Paths:** `docs/architecture/C4.md`, `scripts/architecture_map_contract.py`, `tests/test_architecture_map_contract.py`, `.github/workflows/architecture-map-contract.yml`, `config/workflow_runner_routing_policy.json`
- **Started:** 2026-09-10
- **Last verified:** 2026-09-10 (`38f2b2f`)
- **Summary:** Resolve Issue #1613: Adopt maintainable Mermaid C4 architecture-map contract and automated CI verification. Establish docs/architecture/C4.md with C4Context, C4Container, Feature Map, and Architecture Change Log, validated by scripts/architecture_map_contract.py and test_architecture_map_contract.py.

### DL-0006 · Orchestrator Authentication Perimeter Hardening 1173

- **State:** in_progress
- **Owner:** agent
- **PR:** not created
- **Paths:** `backend/identity.py`, `backend/middleware.py`, `backend/orchestrator_api.py`, `tests/api/test_orchestrator_api.py`
- **Started:** 2026-09-06
- **Last verified:** 2026-09-06
- **Summary:** Resolve Issue #1173: Require authentication on orchestrator state-changing endpoints (/api/orchestrator/lease, /release, /queue control) via require_orchestrator_peer, supporting principal tokens, HUB_FLEET_TOKEN bearer matching, and loopback when DASHBOARD_LOOPBACK_AUTH=1, while rejecting unauthenticated remote callers with HTTP 401.

### DL-0005 · Runner Host Reality vs /tmp Runbook and Profile Cleanup 1159

- **State:** shipped
- **Owner:** agent
- **PR:** #1172
- **Paths:** `deploy/clean-stale-shell-profiles.sh`, `deploy/install-runner-maintenance.sh`, `docs/runbooks/runner-tmp-exhaustion.md`, `tests/deploy/test_clean_stale_shell_profiles.py`
- **Started:** 2026-09-06
- **Last verified:** 2026-09-06
- **Summary:** Resolve Issue #1159: Support standalone maintenance script deployment on hosts without a local repo checkout, document explicit per-host WSL distro names, and add automated pruning of stale cargo/env source statements from ~/.profile and ~/.bashrc.

### DL-0004 · Hub Dashboard Fleet Telemetry Aggregation 1169

- **State:** shipped
- **Owner:** agent
- **PR:** #1171
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
