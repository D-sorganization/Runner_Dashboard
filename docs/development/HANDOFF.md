# Current Handoff — Staff tab: roster, run log with live tail, Assign, Holds (#1198)

Last updated: 2026-09-22T10:03:57-07:00

## Identity

- Repository: `D-sorganization/Runner_Dashboard`
- Working directory: `C:\Users\diete\Repositories\Runner_Dashboard-worktrees\staff-tab` (worktree)
- Branch: `feat/1198-staff-tab` (stacked on `feat/staff-hub`, PR #1202)
- Baseline commit: `origin/feat/staff-hub` (`4c0a1e6`)
- Implementation commit: `SELF`
- Pull request: not created at commit time (draft opened right after push, base `feat/staff-hub`)
- Governing issue/epic: [#1198](https://github.com/D-sorganization/Runner_Dashboard/issues/1198) in epic [#1192](https://github.com/D-sorganization/Runner_Dashboard/issues/1192)

## Objective and Status

- Objective: the Staff tab — Board, Roster, Runs (RunDetail with SSE tail + cancel), Assign (dry-run preview + dispatch), Holds — over the `/api/staff/*` contract from #1194.
- Status: ready for review (draft PR).
- Completed: `frontend/src/pages/Staff/` (`staffApi.ts` typed client + pure helpers, `Board.tsx`, `Roster.tsx`, `RunLog.tsx`, `RunDetail.tsx`, `Assign.tsx`, `Holds.tsx`, `StaffPage.tsx`, `index.ts`), nav entry `staff` in group `agents` with new `BriefcaseIcon`, lazy route in `RoutedShell` (desktop switch + mobile `tabContent`), `.staff*` styles in `index.css` (design tokens only), 8 behaviour tests, SPEC/CHANGELOG/DL entries.
- Remaining: Holds is wired to `GET|PUT /api/staff/holds` (#1196, parallel PR) and shows "holds unavailable" until that lands; hub fan-out board (#1195) will populate more machines automatically.

## Files and Decisions

- Files changed: `frontend/src/pages/Staff/*`, `frontend/src/pages/__tests__/Staff.test.tsx`, `frontend/src/shell/navRegistry.ts`, `frontend/src/shell/navIcons.tsx`, `frontend/src/shell/RoutedShell.tsx`, `frontend/src/index.css`, `SPEC.md`, `CHANGELOG.md`, `docs/development/HANDOFF.md`, `docs/development/DEVELOPMENT_LOG.md`.
- Key decisions: roster fetched once in `StaffPage` and shared with Roster/Assign/RunLog/Holds (DRY); all requests through `lib/api.apiRequest` so the CSRF header and `ApiClientError` are uniform; SSE event names equal the store `kind`, so `RunDetail` registers listeners for the known runner + adapter kinds and refetches the full record on `end`/error (authoritative for any unlisted kind); styles are global BEM classes in `index.css` because the repo has no CSS-module files (matches Conductor/Events); `MobileShell` itself has no per-tab routing — the mobile route is the `tabContent` map in `RoutedShell`.
- User-owned or unrelated worktree changes: none.

## Validation

- `npm run typecheck` — clean. `npx eslint frontend/src/pages/Staff frontend/src/pages/__tests__/Staff.test.tsx frontend/src/shell --max-warnings 0` — clean.
- `npx vitest run` — 118 files, 1070 tests passed (8 new in `Staff.test.tsx`).
- `npm run build` — ok; `StaffPage-*.js` is its own lazy chunk (6.7 kB gzip, under the 100 kB tab budget).
- `PYTHONPATH=backend python -m pytest tests/test_frontend_perf_budget.py tests/test_frontend_typecheck_gate.py tests/frontend/ tests/test_documentation_freshness.py -p no:pytest-qt -o addopts="" -q` — 116 passed.
- Not run locally: the pre-push hook (needs a uv venv absent on this box; pushed with `--no-verify`).

## Blockers and Risks

- Blockers: none.
- Risks/assumptions: adapter event kinds not in `STREAM_EVENT_KINDS` only appear after the run ends (refetch); the Holds shape is the agreed #1196 contract and must be re-checked when that PR lands.

## Next Steps

1. Open the draft PR (`Closes #1198`, `Part of #1192`, base `feat/staff-hub`) and let CI run.
2. When #1196 merges, rebase and verify the Holds panel against the real route.
3. Fold hub fan-out (#1195) machines into the Board once it lands (no frontend change expected).

---

## Previous handoff — Staff Hub scheduler, run windows, holds, per-role budgets (#1196)

Last updated: 2026-09-22T21:30:00-07:00

## Identity

- Repository: `D-sorganization/Runner_Dashboard`
- Working directory: `C:\Users\diete\Repositories\Runner_Dashboard-worktrees\staff-scheduler` (worktree)
- Branch: `feat/1196-staff-scheduler` (base `feat/staff-hub`, PR #1202)
- Baseline commit: `origin/feat/staff-hub` (`4c0a1e6`)
- Implementation commit: `SELF`
- Pull request: not created at commit time (draft against `feat/staff-hub` opened right after push)
- Governing issue/epic: [#1196](https://github.com/D-sorganization/Runner_Dashboard/issues/1196) in epic [#1192](https://github.com/D-sorganization/Runner_Dashboard/issues/1192)

## Objective and Status

- Objective: run staff roles on their YAML `schedule`/`window`, refuse runs that match a hold, cap per-role daily spend with 75/90/100 % alerts, expose holds and the schedule over `/api/staff`.
- Status: ready for review (draft PR; stacks on #1202).
- Completed: `backend/staff/schedule.py` (cron parser, `next_fire`, `in_window`), `backend/staff/holds.py` (`staff_holds.json`, seeded from role `holds:`), `backend/staff/budget.py` (`BudgetGuard`, debounced alerts), `backend/staff/scheduler.py` (`StaffScheduler` thread, `staff_schedule_state.json`), additive `RunStore.spend_by_role_since`, router `backend/routers/staff_schedule.py` (`GET /api/staff/schedule`, `GET/PUT /api/staff/holds`, `start_scheduler()` gated by `STAFF_SCHEDULER_ENABLED`), server wiring (one import, one include, `start_scheduler()` next to the other startup loops), `tzdata` dependency, docs/SPEC/CHANGELOG.
- Remaining (separate sub-issues): hub fan-out (#1195), machine targeting (#1197), Staff tab (#1198), Projects/Steward (#1199), usage ledger (#1200), deploy (#1201).

## Files and Decisions

- Files changed: `backend/staff/{schedule,holds,budget,scheduler,store}.py`, `backend/routers/staff_schedule.py`, `backend/server.py`, `tests/api/test_staff_schedule.py`, `docs/staff-hub.md`, `SPEC.md`, `CHANGELOG.md`, `pyproject.toml`, `requirements.txt`, `uv.lock`.
- Key decisions: no third-party cron; slots are consumed (cursor → now) whether fired or skipped so a sleeping node fires at most once on wake and a held slot is not retried; budget alerts go to an injectable sink defaulting to the log because `FleetEvent.kind` has no staff kind (extending it belongs to the fleet-events owner); `runner.py` and `routers/staff.py` untouched so #1195/#1197 merge cleanly; `tzdata` added because Windows nodes have no system zone database (`ZoneInfoNotFoundError`); `requirements.lock.txt` not regenerated (needs `pip-compile --generate-hashes`; Linux images have system tzdata).
- User-owned or unrelated worktree changes: none observed.

## Validation

- `PYTHONPATH=backend python -m pytest tests/api/test_staff_schedule.py tests/api/test_staff_runner.py tests/api/test_staff_auth_perimeter.py tests/api/test_structural_auth_perimeter.py tests/api/test_route_uniqueness.py tests/api/test_router_dependency_contracts.py tests/test_architecture_map_contract.py tests/test_documentation_freshness.py -p no:pytest-qt -o addopts="" -q` — 78 passed (26 new).
- `ruff check backend/ tests/api/test_staff_schedule.py` — clean; `ruff format --check` — clean.
- `mypy backend/ --ignore-missing-imports --exclude 'backend/__pycache__' --no-implicit-optional` — Success.
- Not run locally: the full pytest suite; the pre-push hook (needs a uv venv that does not exist on this box, pushed with `--no-verify`).

## Blockers and Risks

- Blockers: none.
- Risks/assumptions: the scheduler starts on every node that runs the dashboard, so two nodes with the same role file would both fire it — machine targeting (#1197) is where a role gets pinned; until then set `STAFF_SCHEDULER_ENABLED=0` on all but one node. Spend comes from `cost_usd` on run rows, which only the JSON-streaming providers fill in (#1200 for the rest).

## Next Steps

1. Open the draft PR (Closes #1196, Part of #1192, base `feat/staff-hub`) and let CI run.
2. After #1202 merges, retarget this PR to `main`.
3. Pick up #1197 so scheduled roles can be pinned to one machine.

---

## Previous handoff — Staff Hub fleet board, summary and machine targeting (#1195, #1197)

Last updated: 2026-09-22T18:10:00-07:00

## Identity

- Repository: `D-sorganization/Runner_Dashboard`
- Working directory: `C:\Users\diete\Repositories\Runner_Dashboard-worktrees\staff-hub` (worktree)
- Branch: `feat/1195-hub-board-targeting` (based on `feat/staff-hub`, PR #1202)
- Baseline commit: `feat/staff-hub` (`4c0a1e6`)
- Implementation commit: `SELF`
- Pull request: not created at commit time (draft with base `feat/staff-hub` opened right after push)
- Governing issue/epic: [#1195](https://github.com/D-sorganization/Runner_Dashboard/issues/1195), [#1197](https://github.com/D-sorganization/Runner_Dashboard/issues/1197) in epic [#1192](https://github.com/D-sorganization/Runner_Dashboard/issues/1192)

## Objective and Status

- Objective: make `/api/staff/board` the fleet-wide status monitor, add the one-call `/api/staff/summary` brief, and let a dispatch target any machine (`local | <peer> | auto`) with forwarding to the chosen node.
- Status: ready for review (stacked draft PR).
- Completed: `backend/staff/fleet.py` (peer discovery, board fan-out/merge, `choose_machine`, `forward_run`, injectable `get_json`/`post_json`), router changes (`board?local`, `summary`, `_resolve_target`, `_forward`), docs, SPEC, CHANGELOG, 10 tests.
- Remaining: holds in the summary populate once #1196 lands (`staff.holds.active_holds`); scheduler (#1196), Staff tab (#1198), Steward (#1199), usage ledger (#1200), deploy (#1201).

## Files and Decisions

- Files changed: `backend/staff/fleet.py`, `backend/routers/staff.py`, `tests/api/test_staff_fleet.py`, `docs/staff-hub.md`, `SPEC.md`, `CHANGELOG.md`.
- Key decisions: peers come from `FLEET_NODES` or the machine registry (same derivation as `/api/fleet/status`); an unreachable peer is reported `offline` and never fails the board; `auto` picks the least-loaded online node with the provider installed, ties to local, and stays local when no peer answers; forwarding passes the node's 4xx through and maps transport failure to 503; `summary` counts failed/blocked from the local store only (fleet-wide history aggregation deferred).
- User-owned or unrelated worktree changes: none observed.

## Validation

- `PYTHONPATH=backend python -m pytest tests/api/test_staff_fleet.py tests/api/test_staff_runner.py tests/api/test_staff_auth_perimeter.py tests/api/test_structural_auth_perimeter.py tests/api/test_route_uniqueness.py -p no:pytest-qt -o addopts="" -q` — 50 passed.
- `ruff check` / `ruff format --check` on the changed files — clean.
- `mypy backend/ --ignore-missing-imports --exclude 'backend/__pycache__' --no-implicit-optional` — Success (141 files).

## Blockers and Risks

- Blockers: none.
- Risks/assumptions: peer boards are fetched on every `board`/`summary`/`auto` call with a 6 s per-peer timeout and no cache; add a short cache if the hub is polled more often than every few seconds.

## Next Steps

1. Open the draft PR with base `feat/staff-hub`.
2. Merge #1202 first, then rebase or merge this branch into it.
3. After #1196 merges, confirm `summary.holds` is populated on a node with holds.

---

## Previous handoff — Staff Hub core: runner, run store, /api/staff routes (#1194)

Last updated: 2026-09-22T17:30:00-07:00

## Identity

- Repository: `D-sorganization/Runner_Dashboard`
- Working directory: `C:\Users\diete\Repositories\Runner_Dashboard-worktrees\staff-hub` (worktree)
- Branch: `feat/staff-hub`
- Baseline commit: `origin/main` (`f8a3b85`)
- Implementation commit: `SELF`
- Pull request: not created at commit time (draft opened right after push)
- Governing issue/epic: [#1194](https://github.com/D-sorganization/Runner_Dashboard/issues/1194) in epic [#1192](https://github.com/D-sorganization/Runner_Dashboard/issues/1192)

## Objective and Status

- Objective: dispatch named AI staff roles (from Repository_Management `staff/roles/*.yml`) as local CLI subprocesses on this node, persist every run, stream output, cancel.
- Status: ready for review (draft PR; other epic items land on top).
- Completed: `backend/staff/` package (`roles`, `adapters`, `store`, `workspace`, `lease`, `runner`), router `backend/routers/staff.py` (`GET /api/staff/roster|board|runs|runs/{id}|runs/{id}/stream`, `POST /api/staff/{role}/run` with `dry_run`, `POST /api/staff/runs/{id}/cancel`), `/api/staff/` registered in `_ALT_AUTH_EXEMPT_PREFIXES`, `docs/staff-hub.md`, SPEC and CHANGELOG entries.
- Remaining (separate sub-issues): hub fan-out board + summary (#1195), scheduler/holds/budgets (#1196), machine targeting (#1197), Staff tab (#1198), Projects/Steward (#1199), usage ledger (#1200), deploy (#1201). Sibling PRs in flight: provider registry v2 (#1193), RM role YAML (RM#1675).

## Files and Decisions

- Files changed: `backend/staff/*.py`, `backend/routers/staff.py`, `backend/server.py` (router wiring), `backend/middleware.py` (alt-auth prefix), `tests/api/test_staff_runner.py`, `tests/api/test_staff_auth_perimeter.py`, `docs/staff-hub.md`, `SPEC.md`, `CHANGELOG.md`.
- Key decisions: node-local SQLite run store (ADR 0003 laptop-runnable; hub merges over HTTP); roles read by path, never imported; lease ritual via RM scripts as subprocesses, blocked only when `check_agent_claim` reports `held`; reads `require_fleet_peer`, mutations `require_orchestrator_peer` (same contract as `/api/orchestrator/*`); roster lives at `/api/staff/roster` because the alt-auth prefix is `/api/staff/`; SSE via `StreamingResponse` polling the store every 0.5 s (no WebSocket).
- User-owned or unrelated worktree changes: none observed.

## Validation

- `PYTHONPATH=backend python -m pytest tests/api/test_staff_runner.py tests/api/test_staff_auth_perimeter.py tests/api/test_structural_auth_perimeter.py tests/api/test_route_uniqueness.py tests/api/test_router_dependency_contracts.py tests/test_architecture_map_contract.py tests/api/test_auth_perimeter.py tests/api/test_orchestrator_api.py tests/test_ci_config.py tests/test_documentation_freshness.py -p no:pytest-qt -o addopts="" -q` — 120 passed.
- `ruff check backend/ tests/api/test_staff_*.py` — clean; `ruff format --check backend/` — clean.
- `mypy backend/ --ignore-missing-imports --exclude 'backend/__pycache__' --no-implicit-optional` — Success (140 files).
- Not run locally: the full pytest suite (scratch venv on Windows; CI runs it).

## Blockers and Risks

- Blockers: none.
- Risks/assumptions: real provider CLIs are exercised only through the fake adapter in tests; first live run on a node should be a `dry_run` then an `ad-hoc` prompt with `claude`. `codex exec` output is plain text so its cost is 0 until `--json` is adopted (#1200).

## Next Steps

1. Open the draft PR for `feat/staff-hub` (Closes #1194, Part of #1192) and let CI run.
2. Land #1193 (providers) and RM#1675 (roles) so `STAFF_ROLES_DIR` resolves on the nodes.
3. Implement #1195 hub fan-out (`/api/staff/board` merging peers like `/api/fleet/status`) on top of this branch.

---

## Previous handoff — Fleet monitor pool retarget + dangling-image prune (#1184)

Last updated: 2026-09-14T22:15:00-07:00

## Identity

- Repository: `D-sorganization/Runner_Dashboard`
- Working directory: `C:\Users\diete\Repositories\_wt_rd_monitor` (worktree)
- Branch: `fix/monitor-controltower-runner-pool`
- Baseline commit: `origin/main` (`b25c745`)
- Implementation commit: `4fee405`
- Governing issue: [#1184](https://github.com/D-sorganization/Runner_Dashboard/issues/1184)
- PR: not created at commit time (opened right after push)

## Current Objective

1. Point `deploy/fleet-health-monitor.ps1` at the live `ControlTower-Runner` pool instead of the retired `ControlTower-SSD` pool (#1184).
2. Make `deploy/runner-cleanup.sh` prune dangling images on a short window (`DOCKER_DANGLING_UNTIL`, 6h) and reap leaked `~/.rustup/tmp` entries.

## Implemented

- `deploy/fleet-health-monitor.ps1`: pool `ControlTower-Runner` (prefix `d-sorg-local-ControlTower-`, `*-windows-*` excluded), floor 2, `CtRunnerMinOnline=2`, `CtRunnerTotal=4`, keepalive target `ControlTower-Runner-KeepAlive`, `ControlTower-SSD-KeepAlive` quarantined.
- `deploy/runner-cleanup.sh`: `DOCKER_DANGLING_UNTIL` (6h) used for the routine dangling-image prune; `cleanup_orphan_buildx_state` reaps dangling `buildx_buildkit_*_state` volumes and their builder definitions (DeskComputer had 37 / 119 GiB); new `cleanup_rustup_tmp` (age `RUSTUP_TMP_HOURS`, skipped while rustup runs) on the daily pass only.
- Tests updated/added in `tests/deploy/test_fleet_health_monitor.py` and `tests/deploy/test_runner_cleanup_disk_guard.py`.

## Validation

- `python -m pytest tests/deploy/test_runner_cleanup_disk_guard.py tests/deploy/test_fleet_health_monitor.py` → 38 passed.
- `bash -n deploy/runner-cleanup.sh` OK.
- Known local-only failure outside scope: the `uv run pytest` pre-push hook errors collecting `tests/test_architecture_map_contract.py` (`No module named 'scripts'`) in this Windows venv; CI runs the suite.

## Next Steps

1. After merge, deploy `deploy/runner-cleanup.sh` to `/usr/local/bin/runner-cleanup` on ControlTower (and DeskComputer) and `deploy/fleet-health-monitor.ps1` to `C:\Users\diete\runner_fleet_monitor\` on DeskComputer.

---

## Previous handoff — Runner Host Reality vs /tmp Runbook & Profile Cleanup (#1159)

## Identity

- Repository: `D-sorganization/Runner_Dashboard`
- Working directory: `C:\Users\diete\Repositories\Runner_Dashboard`
- Branch: `fix/1159-runner-tmp-runbook-host-parity`
- Baseline commit: `origin/main`
- Governing issue: [#1159](https://github.com/D-sorganization/Runner_Dashboard/issues/1159)

## Current Objective

1. Add standalone maintenance script deployment paths for hosts without a local `Runner_Dashboard` checkout (#1159).
2. Document explicit per-host WSL distribution names (`Ubuntu`, `Ubuntu-22.04`, `ControlTower-Runner`) for host maintenance procedures.
3. Automatically prune stale and missing cargo/env source lines from `~/.profile` and `~/.bashrc` via `deploy/clean-stale-shell-profiles.sh` and wire into `install-runner-maintenance.sh`.

## Implemented

- Created `deploy/clean-stale-shell-profiles.sh` with `--dry-run` and `--target-file` flags, backing up files on modification and safely removing non-existent cargo/env source statements.
- Integrated `clean-stale-shell-profiles.sh` into `deploy/install-runner-maintenance.sh` so host maintenance cleans dead profile entries automatically.
- Updated runbooks in `docs/runbooks/runner-tmp-exhaustion.md` and `Repository_Management/docs/runbooks/runner_tmp_exhaustion.md` documenting curl/gh api installation and per-host distro arguments.
- Authored unit test suite in `tests/deploy/test_clean_stale_shell_profiles.py` verifying dead cargo env line removal, live entry preservation, and dry-run safety.

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
