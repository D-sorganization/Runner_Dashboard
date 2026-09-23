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

### DL-#1229 · Fleet Coordination API (sessions, messages, claims, briefing)

- **State:** in_review
- **Owner:** claude
- **Issue:** #1229 (epic #1192)
- **Branch:** `feat/coordination-api`
- **PR:** opened after push
- **Paths:** `backend/coordination/`, `backend/routers/coordination.py`, `backend/staff/lease.py`, `backend/staff/usage.py`, `backend/identity.py`, `backend/middleware.py`, `tests/api/test_coordination_*.py`, `docs/coordination-api.md`, `frontend/src/lib/openapi.json`, `frontend/src/lib/api-types.ts`
- **Started:** 2026-09-22
- **Last verified:** 2026-09-22 (`9fd440d` + this change; `pytest tests/api -k "coordination or staff or auth"` 220 passed / 2 skipped; full tests/api 702 passed, 1 timing flake in test_staff_runner passes on rerun; mypy backend/ clean)
- **Summary:** One HTTP surface for every agent: board sessions + staff runs, inbox, presence, messages, claims (409 when held) and a pre-work briefing, all through the RM scripts (shared subprocess helper extracted from the staff lease ritual). Writes need `coordination.write` or the loopback orchestrator peer.
- **Next step:** Merge, then switch the board read to `list --all-repos` on nodes once the RM change lands (the fallback already covers older RM checkouts).

### DL-#1228 · Fleet API agent clients (client, fleetctl, MCP server)

- **State:** in_review
- **Owner:** claude
- **Issue:** #1228 (epic #1192)
- **Branch:** `feat/fleet-clients`
- **PR:** opened after push
- **Paths:** `clients/fleet/`, `tests/clients/`, `docs/agents/connect.md`, `.github/workflows/ci-standard.yml`, `tests/test_ci_config.py`
- **Started:** 2026-09-22
- **Last verified:** 2026-09-22 (`26ffa70` + this change; `pytest tests/clients` 66 passed on the WSL venv; ruff 0.14.10 check/format and mypy 1.13.0 clean on `clients/`)
- **Summary:** Stdlib-only `FleetClient`, `fleetctl` CLI and hand-written MCP stdio server (15 `fleet_*` tools) generated from one command table, so Claude Code, Codex, Gemini CLI and Grok Bot share the Fleet Coordination API contract v1. The coordination/priorities server side is being built in parallel; the clients follow the contract paths.
- **Next step:** After the coordination/priorities server PR merges, run `fleetctl briefing --repo Runner_Dashboard` against DeskComputer and register the MCP server in Claude Code, Codex and Gemini.

### DL-#1223 · Staff Hub node setup documentation

- **State:** in_review
- **Owner:** claude
- **Issue:** #1223 (epic #1192)
- **Branch:** `docs/staff-hub-node-setup`
- **PR:** opened after push
- **Paths:** `docs/staff-hub.md`
- **Started:** 2026-09-22
- **Last verified:** 2026-09-22 (`c12a7dc` + this change; the fix was reproduced and verified on DeskComputer with `systemd-run` under the unit sandbox)
- **Summary:** Node setup for staff roles (drop-in, `CLAUDE_CONFIG_DIR`, `GIT_CONFIG_GLOBAL`, Linux clones) moves from operator scripts into the Staff Hub doc, including why a read-only home blocks Claude token refresh.
- **Next step:** Merge; apply the same setup on ControlTower and OGLaptop.

### DL-#1234 · Fleet API follow-ups

- **State:** in_review
- **Owner:** claude
- **Issue:** #1234 (epic #1192)
- **Branch:** `fix/fleet-api-followups`
- **PR:** opened after push
- **Paths:** `backend/staff/holds.py`, `clients/fleet/fleet_client.py`, `clients/fleet/fleet_tools.py`, `tests/clients/`, `tests/conftest.py`, `tests/api/test_staff_schedule.py`, `pyproject.toml`
- **Started:** 2026-09-23
- **Last verified:** 2026-09-23 (main + this change; `pytest tests/api tests/clients` together: 733 passed, 7 skipped, cancel-timing test deselected)
- **Summary:** Summary holds were always empty; client presence contract drifted from the server; a second conftest broke combined test runs.
- **Next step:** Merge.

### DL-#1225 · Staff runs skip issues with an open linked PR

- **State:** in_review
- **Owner:** claude
- **Issue:** #1225 (epic #1192)
- **Branch:** `fix/staff-skip-linked-issues`
- **PR:** opened after push
- **Paths:** `backend/staff/workspace.py`, `tests/api/test_staff_fleet_rules.py`
- **Started:** 2026-09-22
- **Last verified:** 2026-09-22 (`c12a7dc` + this change; tests/api/test_staff_fleet_rules.py 3 passed)
- **Summary:** run-905a8b3586a7 opened Gasification_Model#5061 for #5059 while #5060 was already open for it. The built-in fleet rules now say to skip issues an open PR references.
- **Next step:** Merge and redeploy DeskComputer.

### DL-#1221 · Staff Hub unattended runs

- **State:** in_review
- **Owner:** claude
- **Issue:** #1221 (epic #1192)
- **Branch:** `fix/staff-unattended-runs`
- **PR:** opened after push
- **Paths:** `backend/staff/adapters.py`, `backend/staff/workspace.py`, `backend/staff/scheduler.py`, `backend/staff/runner.py`, `tests/api/test_staff_runner.py`, `tests/api/test_staff_schedule.py`, `tests/api/test_staff_fleet_rules.py`
- **Started:** 2026-09-22
- **Last verified:** 2026-09-22 (`a41473d` + this change; 100 staff tests passed twice in WSL Python 3.12; the Windows-only cancel-test timing flake also occurs on main)
- **Summary:** The first scheduled Night Watch stopped to ask permission to commit, ran on the CLI default model, could not see its playbook, and was recorded as succeeded. Fixed with bypassPermissions + sonnet default, an inlined playbook, a concrete rotating scheduled prompt, and failure when no `STAFF_RESULT` line is printed.
- **Next step:** Merge, rebuild the DeskComputer install from main, and confirm the next scheduled slot opens a draft PR.

### DL-#1217 · Staff Hub fleet-rule guardrails

- **State:** in_review
- **Owner:** claude
- **Issue:** #1217 (epic #1192; companion Repository_Management#1700)
- **Branch:** `fix/staff-fleet-rules-guardrails`
- **PR:** opened after push
- **Paths:** `backend/staff/workspace.py`, `tests/api/test_staff_fleet_rules.py`
- **Started:** 2026-09-22
- **Last verified:** 2026-09-22 (`26b238f` + this change; 45 passed across staff fleet-rules/consolidation/schedule tests)
- **Summary:** Role files carried agent guardrails as `holds:`, which the scheduler seeds as blocks; RM#1701 removes them and this change puts the missing two (claim:local/leases, no bulk issue filing) into the built-in `FLEET_RULES` every prompt ends with.
- **Next step:** Merge; no deploy action beyond the next build.

### DL-#1216 · Prune credential-bearing url.insteadOf entries from runner ~/.gitconfig

- **State:** shipped
- **Owner:** claude
- **Issue:** #1216 (part of the #1192 rollout; source fixed by Gasification_Model#5060)
- **Branch:** `fix/1216-prune-gitconfig-insteadof`
- **PR:** #1219
- **Paths:** `deploy/clean-gitconfig-token-rewrites.sh`, `deploy/install-runner-maintenance.sh`, `tests/deploy/test_clean_gitconfig_token_rewrites.py`
- **Started:** 2026-09-22
- **Last verified:** 2026-09-22 (squash-merged to main via #1219)
- **Summary:** New `clean-gitconfig-token-rewrites` removes every `url` section whose http(s) URL embeds userinfo (insteadOf/pushInsteadOf) from the runner user's global git config with a 0600 timestamped backup, `--dry-run`, idempotent re-runs and redacted logging; installed and run by `install-runner-maintenance.sh` beside the #1159 profile cleanup.
- **Next step:** Run `clean-gitconfig-token-rewrites --dry-run` then a real run on fleet hosts as the runner user.

### DL-#1212 · Artifact wheelhouse ABI contract and fail-closed install

- **State:** in_review
- **Owner:** claude
- **Issue:** #1212 (rollout of epic #1192 / #1201)
- **Branch:** `fix/1212-artifact-abi`
- **PR:** #1220
- **Paths:** `deploy/check-wheelhouse-abi.py`, `deploy/python-runtime.sh`, `deploy/install-dashboard-artifact.sh`, `deploy/package-dashboard-artifact.sh`, `.github/workflows/release.yml`, `tests/deploy/test_artifact_install_fail_closed.py`
- **Started:** 2026-09-22
- **Last verified:** 2026-09-22 (rebased onto main after #1219)
- **Summary:** Packaging builds the wheelhouse for an explicit `--python-minor` (release pins 3.12) and fails when any wheel's ABI/platform cannot install on it; the installer runs the ABI check, selects an interpreter that can build a venv with ensurepip (host pip no longer required) and completes an offline install into a staging venv before touching the deploy dir, then swaps `.venv` with restore-on-failure and excludes it from `rsync --delete`.
- **Next step:** Mark the draft PR ready once CI Standard and Spec Check are green.

### DL-#1213 · Staff Hub PR-consolidation strategy

- **State:** in_review
- **Owner:** claude
- **Issue:** #1213 (epic #1192; companion Repository_Management#1690)
- **Branch:** `feat/1213-pr-consolidation`
- **PR:** not created at commit time (draft opened after push)
- **Paths:** `backend/staff/consolidation.py`, `backend/staff/roles.py`, `backend/staff/store.py`, `backend/staff/runner.py`, `backend/staff/scheduler.py`, `backend/staff/workspace.py`, `backend/routers/staff.py`, `frontend/src/pages/Staff/`, `tests/api/test_staff_consolidation.py`
- **Started:** 2026-09-22
- **Last verified:** 2026-09-22 (`eca7381`)
- **Summary:** Optional `strategy.consolidate_when` (`open_prs`, `utilisation_pct`) on a role; the scheduler tick and the dispatch route evaluate it against the repo's open non-draft PRs and fleet utilisation, inject a `consolidate` / `serial` paragraph into the prompt, return `plan.consolidation`, store `strategy_mode` and the parsed `outcome` ("consolidated N PRs into #M") on the run; Staff tab shows the threshold on the roster card and the outcome in the run log.
- **Next step:** Mark the draft PR ready once CI Standard, Spec Check and frontend tests are green.

### DL-#1209 · Staff board scheduled-role liveness

- **State:** in_review
- **Owner:** claude
- **Issue:** #1209 (epic #1192)
- **Branch:** `feat/1209-staff-liveness`
- **PR:** #1211
- **Paths:** `backend/staff/liveness.py`, `backend/staff/fleet.py`, `backend/routers/staff.py`, `backend/fleet_events.py`, `frontend/src/pages/Staff/Board.tsx`, `frontend/src/pages/Staff/staffApi.ts`, `frontend/src/lib/fleetEvents.ts`, `tests/api/test_staff_liveness.py`
- **Started:** 2026-09-22
- **Last verified:** 2026-09-22 (`cde8c32`)
- **Summary:** Per scheduled role, derive last success / last attempt / expected interval and a status `ok | late | dead | never` from the node store and scheduler state; expose `liveness` on the local board and `liveness_alerts` on the hub board and summary; record a `staff_role_dead` fleet event (6 h debounce); list alerts on the Staff tab Board panel.
- **Next step:** Mark the draft PR ready once CI Standard, Spec Check and frontend tests are green.

### DL-#1201 · Release 4.10.0 and Staff Hub health probe

- **State:** in_review
- **Owner:** agent
- **Issue:** #1201
- **Branch:** `chore/1201-release-4.10.0`
- **PR:** not created
- **Paths:** `VERSION`, `pyproject.toml`, `package.json`, `package-lock.json`, `uv.lock`, `frontend/src/lib/openapi.json`, `deploy/fleet-health-monitor.ps1`, `tests/deploy/test_fleet_health_monitor.py`
- **Started:** 2026-09-22
- **Last verified:** 2026-09-22 (`34cfdae`)
- **Summary:** Cut 4.10.0 (first release with the Fleet Staff Hub, epic #1192) so `release.yml` builds the artifact, and make the DeskComputer fleet health monitor probe `/api/staff/board?local=1` so a dead Staff Hub is alarmed instead of silently skipping scheduled roles.
- **Next step:** After the release artifact publishes, install it on DeskComputer with `deploy/update-deployed.sh --artifact` and hand ControlTower/OGLaptop the same command.

### DL-#1193 · Provider registry v2 (antigravity, cursor-agent, maxwell; Jules disabled; node probe)

- **State:** in_review
- **Owner:** agent
- **Issue:** #1193 (epic #1192)
- **Branch:** `feat/1193-provider-registry-v2`
- **PR:** draft, opened after push
- **Paths:** `backend/agent_remediation/provider_registry.py`, `backend/agent_remediation/provider_probe.py`, `backend/agent_remediation/providers.py`, `backend/agent_remediation/planner.py`, `backend/agent_remediation/policy.py`, `backend/routers/providers.py`, `config/agent_remediation.json`, `tests/api/test_providers_registry.py`, `tests/api/test_conductor_constants.py`, `tests/test_agent_remediation.py`
- **Started:** 2026-09-22
- **Last verified:** 2026-09-22 (`SELF`)
- **Summary:** Resolve Issue #1193: add antigravity, cursor_agent (Grok via Cursor subscription) and optional maxwell providers to the canonical registry, mark the retired jules_cli/jules_api entries enabled=false and drop them from the default provider order, and expose a 60 s-cached per-node CLI probe (installed + authenticated, reusing the credentials-router probes) as node_availability + hostname on GET /api/providers/registry.
- **Next step:** Mark the draft PR ready for review once CI Standard and Spec Check are green.

### DL-#1199 · Projects tab: per-repo charter, status and steward runs

- **State:** in_review
- **Owner:** claude
- **Issue:** #1199 (epic #1192)
- **Branch:** `feat/1199-projects-tab` (base `feat/staff-hub`)
- **PR:** #1208
- **Paths:** `backend/projects/`, `backend/routers/projects.py`, `backend/server.py`, `config/projects.json`, `frontend/src/pages/ProjectsPage.tsx`, `frontend/src/pages/Projects/`, `frontend/src/shell/navRegistry.ts`, `frontend/src/shell/navIcons.tsx`, `frontend/src/shell/RoutedShell.tsx`, `tests/api/test_projects_router.py`, `frontend/src/pages/__tests__/Projects.test.tsx`, `docs/projects.md`
- **Started:** 2026-09-22
- **Last verified:** 2026-09-22 (`6b775d6` + this change; 37 passed in tests/api/test_projects_router.py + perimeter/uniqueness/C4 contract; vitest 1066 passed; ruff, mypy, tsc, eslint, vite build clean)
- **Summary:** `/api/projects` fetches `docs/project/CHARTER.md` + `STATUS.md` per configured repo via `gh_utils.gh_api`, parses them with a parser mirrored from Repository_Management `shared_scripts/project_charter.py` (columns pinned by test), joins the newest `project-steward` staff run, caches 10 min, never 5xx for one bad repo. Projects tab (nav `projects`) renders a card per repo with a stacked progress bar, decisions needed, last-run link and a "Run steward now" POST to `/api/staff/project-steward/run`.
- **Next step:** Open the draft PR against `feat/staff-hub` (Closes #1199) and let CI run.

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

### DL-#1200 · Staff Hub usage ledger: pricing, /api/staff/usage, RM credit_usage export

- **State:** in_review
- **Owner:** agent
- **Issue:** #1200
- **Branch:** `feat/1200-staff-usage` (base `feat/staff-hub`)
- **PR:** not created
- **Paths:** `backend/staff/pricing.py`, `backend/staff/usage.py`, `backend/staff/store.py`, `backend/staff/runner.py`, `backend/routers/staff_usage.py`, `backend/server.py`, `tests/api/test_staff_usage.py`, `docs/staff-hub.md`
- **Started:** 2026-09-22
- **Last verified:** 2026-09-22 (`6b775d6`)
- **Summary:** Epic #1192 usage ledger: price each finished staff run (CLI-reported cost kept as `reported`, else USD-per-1M-token table, else wall-time rate from `STAFF_WALL_USD_PER_MIN`), record `cost_method`, aggregate by provider/role/day with a daily budget from `STAFF_BUDGET_USD_PER_DAY`, and append daily per-provider totals to Repository_Management `data/credit_usage.json` by running RM `scripts/append_credit_usage.py` as a subprocess.
- **Next step:** Open the draft PR against `feat/staff-hub` and the companion RM draft PR, then let CI run.

### DL-#1198 · Staff tab: roster, run log with live tail, Assign, Holds

- **State:** in_review
- **Owner:** agent
- **Issue:** #1198
- **Branch:** `feat/1198-staff-tab`
- **PR:** not created
- **Paths:** `frontend/src/pages/Staff/`, `frontend/src/pages/__tests__/Staff.test.tsx`, `frontend/src/shell/navRegistry.ts`, `frontend/src/shell/navIcons.tsx`, `frontend/src/shell/RoutedShell.tsx`, `frontend/src/index.css`
- **Started:** 2026-09-22
- **Last verified:** 2026-09-22 (`4c0a1e6`)
- **Summary:** Epic #1192 operator surface: Staff tab with Board (10 s poll), Roster cards, Runs log with RunDetail SSE tail and cancel, Assign form with dry-run plan preview and dispatch, and Holds editor (404-tolerant until #1196 lands), routed as nav entry `staff` in the agents group.
- **Next step:** Open the draft PR against `feat/staff-hub` and re-verify Holds once #1196 merges.

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

## Shipped (Last 90 Days)

Entries stay here for 90 days after merge, then move to the archive.

## Archive

Older entries live in `DEVELOPMENT_LOG_ARCHIVE_<year>.md`.
