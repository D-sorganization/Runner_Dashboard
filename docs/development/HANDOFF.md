# Current Handoff — Hub Dashboard Fleet Telemetry Aggregation & Stale Registry Validation (#1169)

Last updated: 2026-09-06T09:25:00-07:00

## Identity

- Repository: `D-sorganization/Runner_Dashboard`
- Working directory: `C:\Users\diete\Repositories\Runner_Dashboard`
- Branch: `fix/1169-hub-fleet-aggregation`
- Baseline commit: `1cabb01`
- Governing issue: [#1169](https://github.com/D-sorganization/Runner_Dashboard/issues/1169)

## Current Objective

1. Add startup registry validation ensuring every non-retired machine and runner pool resolves to a reachable/valid dashboard URL and configuration (#1169).
2. Ensure `/api/fleet/status` on the Hub dashboard aggregates telemetry across all healthy machines (`DeskComputer`, `OGLaptop`, `ControlTower-Runner`) and identifies the local pool by its active name (`ControlTower-Runner`) rather than the retired `ControlTower-NVMe`.
3. Ensure graceful handling of offline or connection-refused nodes without dropping telemetry from healthy nodes.

## Implemented

- Added `assert_valid_active_registry` to `backend/fleet_autoconfig.py` and wired into `backend/server.py` (`_startup` and autoderivation boundary) to fail fast on invalid URLs, missing machine/pool names, or duplicate port assignments on active pools.
- Updated `derive_pool_topology` in `backend/fleet_autoconfig.py` to filter out retired runner pools (`retired: true`), ensuring the local pool resolves to `ControlTower-Runner` and prevents phantom peer probing of retired pools.
- Updated `_iter_registry_entries` in `backend/machine_registry.py` to skip retired entries so they are not treated as missing active machines.
- Unified `FLEET_NODES` across `backend/server.py`, `backend/dashboard_config`, and `backend/routers/fleet.py`, enabling registry autoderivation in `routers/fleet.py` when `FLEET_NODES` is empty and autoderivation is enabled.
- Authored comprehensive unit tests in `tests/test_hub_fleet_aggregation.py` and `tests/test_fleet_autoconfig.py` covering multi-node aggregation, connection refused offline handling, and startup assertions.

## Validation

- `ruff check .` passes with zero errors.
- `mypy backend/` and test type checking pass cleanly.
- `pytest -v tests/test_hub_fleet_aggregation.py tests/test_fleet_autoconfig.py` passes 20/20.

## Next Steps

1. Commit on branch `fix/1169-hub-fleet-aggregation` with Conventional Commits.
2. Push branch to origin.
3. Open PR via GitHub CLI referencing Issue #1169 and enable auto-merge.

## Issue #1141 — OGLaptop production browser OAuth readiness

- Base: protected `main`
- Source slice: call-time typed OAuth configuration, exact MagicDNS origin and
  callback, explicit callback binding for authorization and token exchange,
  no dev-login fallback, redacted health diagnostic, and controlled operator
  provisioning/rotation/rollback documentation.
- Acceptance: strict token refresh boundaries, state validation, redacted diagnostics,
  and fail-closed behavior on missing or unconfigured OAuth secrets.

## Issue #1139 — Windows WSL keepalive under WSL-capable user principal

- Acceptance: Windows keepalive installer `deploy/install-wsl-keepalive-task.ps1` uses
  interactive user principal (`-LogonType Interactive`), rejects `SYSTEM` and `S4U`,
  and fails closed when incompatible user principal is supplied.

## Issue #1144 — Interactive-Safe DeskComputer 1/2 Schedule

- Acceptance: Schedule aligned to 1 weekday-day / 2 weekend-day / 2 overnight (`max_count: 2`).
  Drain marker fail-closed boundary enforced.

## Issue #1119 — Require All Protected Gates Before Auto-Merge

- Acceptance: Branch protection and ruleset drift detector enforced and verified.

## Issue #1085 — Deterministic offline dashboard deployment

- Acceptance: Artifact packaging and installation with strict checksums, schema-v2 verification,
  and offline wheelhouse support.
