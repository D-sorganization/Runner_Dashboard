# Development Log — Runner_Dashboard

State table for every feature in flight in this repository. Update
entries **in place**; never append dated sections. One entry per
feature, from proposal to ship. See the `development-logs` section of
`AGENTS.md` for the binding rules and
`shared_scripts/development_log.py` for the validator.

- **Portfolio:** infra
- **WIP limit:** 4
- **Last audited:** 2026-09-28 by claude (merged-PR reconciliation; shipped entries archived)

## States

`proposed` → `in_progress` → `in_review` → `shipped`, with `parked`
reachable from any live state and `abandoned` from `parked`.
`shipped` never returns to `in_progress`; open a new entry instead.

## Active

### DL-#1759 — SPA shell no-cache + retired-role roster status

- **State:** in_review
- **Owner:** claude
- **Issue:** #1759
- **Branch:** `fix/spa-cache-retired-status`
- **PR:** not created
- **Paths:** `backend/server.py`, `tests/test_spa_fallback.py`, `frontend/src/pages/StaffConsole/rosterUtils.ts`, `frontend/src/pages/StaffConsole/types.ts`, `frontend/src/pages/StaffConsole/RosterRow.tsx`, `frontend/src/pages/StaffConsole/__tests__/rosterUtils.test.ts`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (WSL `pytest tests/test_spa_fallback.py tests/test_static_serving.py -q` 14 passed; `npx vitest run frontend/src/pages/StaffConsole` 239 passed; `npm run typecheck` and `eslint` clean; `ruff check`/`ruff format --check` clean; mypy clean)
- **Summary:** After a deploy the browser kept running the stale bundle because the SPA shell (`/`, `/t/:tabId`, `/settings/push`) and `/sw.js` were served with no `Cache-Control`, so a cached `index.html` could keep pointing at a superseded hashed `/assets/*` chunk. `serve_index`, `serve_spa_fallback` and `serve_service_worker` now set `Cache-Control: no-cache`; the hashed `/assets/*` `StaticFiles` mount is unchanged. Separately, `computeRoleStatus` never considered `role.retired`, so a retired role showed as "Idle"; it now checks `retired` right after the invalid check and returns a new `"retired"` status (clean) or `"unavailable"` with reason `"retired: <retired_reason>"` (has a reason), with `RosterRow.tsx` labeling it "Retired".
- **Next step:** Open the PR.

### DL-#1755 — Phone sign-in via Tailscale identity headers

- **State:** in_review
- **Owner:** claude
- **Issue:** #1755 (part of epic #1718)
- **Branch:** `fix/tailnet-identity-1755`
- **PR:** #1757
- **Paths:** `backend/tailnet_identity.py`, `backend/identity.py`, `backend/server.py`, `tests/api/test_tailnet_identity.py`, `docs/runbooks/phone-access-tailnet.md`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (WSL `pytest tests/api/test_tailnet_identity.py tests/api -q -k "auth or identity or tailnet or loopback"` all passed; `ruff check`/`ruff format --check` clean; pre-push mypy 1.13 clean)
- **Summary:** `tailscale serve` rewrites the resolved client to the phone's tailnet address, so the loopback-admin bypass correctly refuses it, and with no GitHub OAuth app configured the phone had no way to sign in. `TransportPeerMiddleware` (new `tailnet_identity.py`) records the raw transport peer before uvicorn's `ProxyHeadersMiddleware` rewrites `scope["client"]`; `tailnet_principal` admits a request as a `roles=["loopback"]` principal only when `DASHBOARD_TAILSCALE_AUTH=1`, the raw peer is loopback (rules out a direct tailnet caller forging the header), the resolved client is in a Tailscale range, and `Tailscale-User-Login` is on the `DASHBOARD_TAILSCALE_LOGINS` allow-list. Wired into all four principal-resolution paths in `identity.py`.
- **Next step:** Open the PR as draft.

### DL-#1726 — Seeded role holds are guardrails, not scheduling holds

- **State:** in_review
- **Owner:** claude
- **Issue:** #1726
- **Branch:** `fix/hold-guardrail-kind-1726`
- **PR:** not created
- **Paths:** `backend/staff/holds.py`, `backend/staff/hold_actions.py`, `backend/staff/scheduler.py`, `backend/staff/models.py`, `backend/routers/staff_schedule.py`, `frontend/src/pages/Staff/Holds.tsx`, `frontend/src/pages/StaffConsole/rosterUtils.ts`, `frontend/src/pages/StaffConsole/RosterRow.tsx`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (backend `tests/api/test_staff_schedule.py tests/api/test_staff_v1_holds_schedule.py tests/unit/test_staff_actions.py tests/unit/test_staff_reconcile.py` pass; frontend `pages/Staff` + `pages/StaffConsole` vitest suites, 260 tests, pass; `npm run typecheck` clean; ruff clean)
- **Summary:** `Hold` gained `kind: "guardrail" | "schedule"`. Holds seeded from a role's YAML `holds:` list are guardrails (kept in the role prompt, shown as a standing rule, never block); a hold created via the Holds tab, the holds API, or `staff.hold` is a schedule hold and blocks. `HoldsList.blocking()` (read by the scheduler) only matches schedule holds. A legacy persisted hold with no `kind` migrates to guardrail when its text matches a current seed (text only, so holds naming the retired orchestrator still migrate), else to schedule.
- **Next step:** Open the PR.

### DL-#1728 — Staff threads API tests stop leaking chat turns and staff-run threads

- **State:** in_review
- **Owner:** claude
- **Issue:** #1728
- **Branch:** `fix/staff-threads-test-thread-leak`
- **PR:** #1729
- **Paths:** `tests/api/test_staff_threads_api.py`
- **Started:** 2026-09-27
- **Last verified:** 2026-09-28 (file 14 passed with thread-exception warnings as errors; RED without the stub = 2 errors)
- **Summary:** The autouse fixture stubs the background chat turn. Tripwires fail any test that runs a real chat turn, creates a real worktree, or leaves a `staff-run-*` thread alive after teardown.
- **Next step:** Merge PR #1729 once its CI is green.

### DL-#1748 — Diagnostics report the deployed commit on artifact installs

- **State:** in_review
- **Owner:** claude
- **Issue:** #1748
- **Branch:** `fix/diagnostics-artifact-commit`
- **PR:** not created
- **Paths:** `backend/routers/diagnostics.py`, `backend/routers/deployment.py`, `backend/server.py`,
  `tests/api/test_diagnostics_deployed_commit.py`, `tests/api/test_deployment_git_drift.py`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (WSL `pytest tests -q -k "diagnostic or drift"` 114 passed;
  `ruff check`/`ruff format --check` clean; `mypy backend/` clean, 318 files)
- **Summary:** Artifact-installed hubs have no `.git` directory, so `/api/diagnostics/summary` always
  reported `git_commit: "unknown"` and `/api/deployment/git-drift` claimed "up to date" with no
  commits to compare. Diagnostics now falls back to the deployed `git_sha` from deployment metadata
  (via a new `set_deployment_info_getter`, no `server` import); git-drift reports `is_drifted: null`
  and an explicit unknown-state message when either commit is missing.
- **Next step:** Open the PR as draft.

### DL-#1745 — Scheduled workflows inventory loads within the proxy timeout

- **State:** in_review
- **Owner:** claude
- **Issue:** #1745
- **Branch:** `fix/scheduled-workflows-walk`
- **PR:** not created
- **Paths:** `backend/scheduled_workflows.py`, `backend/proxy_utils.py`, `backend/routers/runs_workflows.py`, `tests/test_scheduled_workflows.py`, `tests/api/test_scheduled_workflows_route.py`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (WSL `pytest tests -q -k "scheduled or proxy"` 110 passed; `ruff check`/`ruff format --check` clean; `mypy backend/` clean)
- **Summary:** `collect_inventory` walked repos serially and raw-fetched every workflow YAML (1,200+ requests for 41 repos), never finishing inside the hub's answer budget; the hub's 20 s wait also exceeded the proxy's 15 s timeout, so every non-hub node got a 504 instead of the designed degraded answer. The walk now reads the `.github/workflows` contents listing per repo and caches cron expressions by blob SHA, walks repos concurrently behind a bounded semaphore (6), and the hub's wait is clamped below the new `proxy_utils.HUB_PROXY_TIMEOUT_S` constant.
- **Next step:** Open the PR as draft.

### DL-#1747 — Fleet page shows only real data and honest loading states

- **State:** in_review
- **Owner:** claude
- **Issue:** #1747
- **Branch:** `fix/fleet-honest-panels`
- **PR:** not created
- **Paths:** `frontend/src/pages/OverviewPage.tsx`, `frontend/src/pages/OverviewLeases.tsx` (deleted), `frontend/src/pages/Fleet/FleetAlertsSection.tsx`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (Fleet + Overview vitest 54 passed; typecheck and eslint clean)
- **Summary:** The Fleet page rendered a hard-coded fake "Active Leases" preview and claimed "All systems nominal" before alerts had loaded. The preview is removed and the alerts section shows a checking state while loading.
- **Next step:** Open the PR as draft, then mark it ready and arm auto-merge via `automerge_guard`.

### DL-#1744 — Staff role details show the real schedule window and budget

- **State:** in_review
- **Owner:** claude
- **Issue:** #1744
- **Branch:** `fix/staff-role-details`
- **PR:** not created
- **Paths:** `frontend/src/pages/StaffConsole/rosterUtils.ts`, `frontend/src/pages/StaffConsole/types.ts`, `frontend/src/pages/StaffConsole/useStaffConsole.ts`, `frontend/src/pages/StaffConsole/contextTypes.ts`, `frontend/src/pages/StaffConsole/ContextPane.tsx`, `frontend/src/pages/Staff/Roster.tsx`, `frontend/src/pages/Staff/__tests__/Roster.test.tsx`, `frontend/src/pages/StaffConsole/__tests__/rosterUtils.test.ts`, `frontend/src/pages/StaffConsole/__tests__/useStaffConsole.test.tsx`, `frontend/src/pages/StaffConsole/__tests__/ContextPane.test.tsx`, `frontend/src/pages/StaffConsole/__tests__/Roster.test.tsx`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (`npx vitest run frontend/src/pages/Staff frontend/src/pages/StaffConsole` 257 passed; `npm run typecheck` clean; eslint clean on changed files)
- **Summary:** The Roster's Schedule cell stringified the API's `{start,end}` window object (`[object Object]`), and the Console's role pane read a nonexistent `budget.daily_limit ?? 50` / `spend_today ?? 0`, always showing a fabricated $50/day cap and $0 spend, and never mapped `schedule` at all. A shared `formatRoleWindow` helper formats the window consistently; `toRoleDetail` now maps the real `usd_per_day` and leaves unknown spend as `undefined` (rendered "—"), and maps `schedule` to cron + formatted window + `enabled: !retired`.
- **Next step:** Open the PR as draft.

### DL-#1738 — Fleet page binds runners to machines

- **State:** in_review
- **Owner:** claude
- **Issue:** #1738
- **Branch:** `fix/fleet-machine-binding`
- **PR:** (pending)
- **Paths:** `backend/routers/runners.py`, `frontend/src/pages/Fleet/FleetMachinesSection.tsx`, `frontend/src/pages/Fleet/FleetRunnersSection.tsx`, `frontend/src/pages/CredentialsPage.tsx`, `tests/api/test_runners_machine_field.py`, `frontend/src/pages/Fleet/__tests__/FleetMachinesSection.test.tsx`, `frontend/src/pages/Fleet/__tests__/FleetRunnersSection.test.tsx`, `frontend/src/pages/__tests__/CredentialsPage.test.tsx`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (WSL `pytest tests/api/test_runners_machine_field.py tests/api/test_routers_runners.py` all passed; `npx vitest run frontend/src/pages/Fleet frontend/src/pages/__tests__/CredentialsPage.test.tsx` 72 passed; ruff and eslint clean)
- **Summary:** The 2026-09-28 live sweep found every Fleet machine showing `Runners 0 / 21` (grouped by `name.split("-")[2]`, always `local`, falling back to the org-wide `health.runners_registered`), busy runners showing Current Task `idle` (looked up by a `runner_name` field `/api/runs` never returns), and ready credential providers still showing their `setup_hint`. Backend now stamps a canonical `machine` field via the existing `infer_machine_from_runner_name` parser; the Fleet Machines table groups by it with no org-wide fallback; the Runners table shows `busy`/`-`/`idle` from the runner's own state when no run is known; CredentialsPage hides `setup_hint` once a probe is `usable`.
- **Next step:** Open PR and enable auto-merge.

### DL-#1742 — Queue page does not claim idle before its data arrives

- **State:** in_review
- **Owner:** claude
- **Issue:** #1742
- **Branch:** `fix/queue-loading-state`
- **PR:** #1743
- **Paths:** `frontend/src/pages/Queue/index.tsx`, `frontend/src/pages/Queue/__tests__/QueueLoadingState.test.tsx`, `frontend/src/pages/Queue/__tests__/QueueTab.test.tsx`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (Queue vitest 63 passed; `npm run typecheck` and eslint clean)
- **Summary:** Work → Queue reported an idle fleet and an empty queue while the first load was pending or had failed. Counts now read `—` (loading or unknown) until a payload arrives, and a failed load says so.
- **Next step:** Mark #1743 ready and arm auto-merge via `automerge_guard` once CI is green.

### DL-#1740 — Service worker gets a distinct build id per build

- **State:** in_review
- **Owner:** claude
- **Issue:** #1740
- **Branch:** `fix/sw-build-id`
- **PR:** #1741
- **Paths:** `frontend/src/lib/buildId.ts`, `frontend/src/lib/__tests__/buildId.test.ts`, `vite.config.ts`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (vitest 3 passed; `npm run typecheck` and eslint clean; built bundle registers `sw.js?build=157232e6`)
- **Summary:** The service worker was always registered as `build=dev`, so it never updated after a deploy and its cache never rotated. The build now stamps the git short SHA (or an explicit `VITE_BUILD_ID`, or the build time).
- **Next step:** Mark #1741 ready and arm auto-merge via `automerge_guard` once CI is green.

### DL-#1735 — Frontend calls only routes that exist

- **State:** in_review
- **Owner:** claude
- **Issue:** #1735
- **Branch:** `fix/frontend-route-contract`
- **PR:** #1737
- **Paths:** `backend/routers/workflow_stats.py`, `backend/routers/auth.py`, `backend/server.py`, `frontend/src/hooks/usePollingQueries.ts`, `frontend/src/hooks/useStaffQueries.ts`, `frontend/src/pages/Operations/OperationsDiagnosticsSection.tsx`, `frontend/src/lib/openapi.json`, `frontend/src/lib/api-types.ts`, `tests/frontend/test_frontend_api_routes_exist.py`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (WSL `tests/api tests/frontend` 1425 passed; guard catches a dead `/api/fleet`; tsc, eslint, vitest, ruff, mypy clean)
- **Summary:** An audit of frontend `/api/` literals against backend routes found the Insights workflow-stats routes never wired, a missing `POST /api/auth/refresh`, a dead Diagnostics link and ten dead hooks. Routes wired, link fixed, hooks removed, and a guard test keeps the frontend to routes that exist.
- **Next step:** Mark #1737 ready and arm auto-merge via `automerge_guard`.

### DL-#1734 — Type-check cross-module imports in the backend

- **State:** in_review
- **Owner:** claude
- **Issue:** #1734
- **Branch:** `fix/mypy-resolved-imports`
- **PR:** #1736
- **Paths:** `pyproject.toml`, `.pre-commit-config.yaml`, `backend/push.py`, `backend/staff/followup.py`, `backend/staff/inbox.py`, `backend/staff/work_request_dispatch.py`, `backend/staff/runner_ops.py`, `backend/staff/runner.py`, `backend/staff/maintenance.py`, `backend/routers/staff_v1.py`, `backend/routers/remediation.py`, `backend/routers/runner_diagnostics.py`, `backend/server.py`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (WSL full suite 5298 passed; resolved `mypy backend/` clean on main after #1733)
- **Summary:** mypy named backend modules `backend.x` while the code imports `staff.x`, so cross-module calls were unchecked. Resolving them found 55 errors, including 500s on the v1 run stream, v1 audit and the remediation config save, ImportErrors on work-request dispatch, 502s on runner troubleshoot and schedule-scale, silently dropped watchdog pushes and a broken inbox needs-input source. All fixed; the pyproject config makes CI and pre-push check resolved imports.
- **Next step:** Mark #1736 ready and arm auto-merge via `automerge_guard`.

### DL-#1709 — Staff runner retry nudge for missing STAFF_RESULT line

- **State:** in_progress
- **Owner:** local
- **Issue:** #1709
- **Branch:** `fix/staff-runner-result-nudge-1709`
- **PR:** (pending)
- **Paths:** `backend/staff/adapters.py`, `backend/staff/classifier.py`, `backend/staff/lease.py`, `backend/staff/retry.py`, `backend/staff/runner.py`, `backend/staff/runner_ops.py`, `tests/api/test_staff_runner.py`, `tests/unit/test_staff_classifier.py`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (pytest staff+classifier+runner 59 passed, ruff clean, mypy clean, all files <= 500 LOC)
- **Summary:** Enforces that unattended runs exiting 0 without a STAFF_RESULT line receive one bounded host-level retry nudge resuming the same session; accepts the nudge result line only on an exact prefix match (`STAFF_RESULT:`); records runs that pause on a question as failure_class="needs_input" with `error="agent paused asking: {last_line}"`, lease-blocked runs as failure_class="lease_blocked", and non-question missing result runs as failure_class="no_result" with `error="agent exited 0 without a STAFF_RESULT line"`.
- **Next step:** Open PR and enable auto-merge.

### DL-#1718 — Live-sweep fixes for the UX overhaul

- **State:** in_review
- **Owner:** claude
- **Issue:** #1718
- **Branch:** `feat/ux-overhaul`
- **PR:** see branch
- **Paths:** `backend/cache_utils.py`, `backend/routers/runs_workflows.py`, `backend/projects/service.py`, `backend/routers/repos_stats.py`, `backend/lease_synchronizer.py`, `backend/platform_utils/wsl_paths.py`, `backend/priorities/consensus.py`, `frontend/src/pages/WorkflowsPage.tsx`, `frontend/src/pages/Workflows.tsx`, `frontend/src/components/formatters.ts`, `frontend/src/primitives/IntroHeader.tsx`, `frontend/src/primitives/OwnerMarkdown.tsx`, `frontend/src/pages/MaxwellPage.tsx`, `frontend/src/pages/Projects/*`, `frontend/src/pages/Analysis.tsx`, `frontend/src/pages/Fleet/Mobile.tsx`, `frontend/src/pages/Remediation/*`, `frontend/src/pages/Queue/*`, `frontend/src/pages/Operations/*`, `frontend/src/pages/FleetCommand/PrioritiesPanel.tsx`, `frontend/src/pages/StaffConsole/*`, `frontend/src/pages/decompIcons.tsx`, `tests/frontend/test_no_pictographic_emoji.py`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (vitest StaffConsole, Staff, Projects, Operations folders passed; WSL pytest projects router passed; Playwright journey + a11y 8/8 on Vite; browser pane at 1280x800 and 375x812; WSL pytest stats, lease sync, wsl paths, priorities passed; tsc, eslint, ruff, mypy clean)
- **Summary:** Stale-while-revalidate for slow GitHub aggregates (workflows list, scheduled workflows) so they stop starving the browser's connections; bounded REST calls for the workflows catalogue; durations roll up to hours/days; SVG intro icon; readable Maxwell and Projects errors; decision links render; attention rows name their repo. Mobile pass: Fleet and Remediation read real endpoints, `/api/issues` 500 fixed, stale ages and reasons humanised, Ask button clears the bottom bar, Operations stops refetching, `/api/stats` concurrent, reports path finds the Windows profile, board minutes keep wrapped text. Pictographic emoji replaced by SVG glyphs, guarded by a test. `/api/runs/enriched` slimmed from 755 KB to 147 KB (50 runs) by dropping REST link fields and nested repository noise. Staff checkout discovery finds the Windows profile the same way as the reports path. `/api/runs` slimmed too; phone-friendly 404 hint; ControlTower pool heading only for several pools. Post-merge CI fixes: OpenAPI snapshot, neutral badge contrast, system run cards, inline markdown beside badges. 1280px sweep: the Staff Console role context overlays the conversation (closed by default, with its own close button) instead of wrapping under the roster; the Staff page no longer scrolls sideways; project card headers wrap; project cards name the missing GitHub App permission instead of the raw 403 body; Operations tables scroll sideways on phones instead of clipping. v1 holds/schedule routes (500 since #1386) share the legacy route helpers; routing eval engine moved out of `tests/` so `/api/v1/staff/routing/eval` works on deployed hubs.
- **Next step:** Ship in the consolidated UX PR for epic #1718.

### DL-#1723 — App shell, primitives and settings polish

- **State:** in_review
- **Owner:** claude
- **Issue:** #1723
- **Branch:** `feat/ux-overhaul`
- **PR:** see branch
- **Paths:** `frontend/src/shell/*`, `frontend/src/primitives/*`, `frontend/src/design/fleetThemes.ts`, `frontend/src/pages/Principals.tsx`, `frontend/src/pages/LocalApps.tsx`, `backend/local_app_monitoring.py`, `local_apps.json`
- **Started:** 2026-09-27
- **Last verified:** 2026-09-27 (2026-09-27: design vitest 51 passed (new theme/token parity test); Principals 7, LocalApps 12 passed; test_local_app_monitoring 12 passed; tsc + eslint + ruff + mypy clean; runtime --text-muted #8a8a93 verified in preview)
- **Summary:** Linear-style sidebar and compact top bar; standard Dark/Light themes emit tokens.ts neutrals (inline <html> vars had undone the 4.5:1 muted text); Principals shows 'Admin access required' on 403; artifact installs report deployed sha instead of git probe errors; local_apps.json points at Runner_Dashboard.
- **Next step:** Ship in the consolidated UX PR for epic #1718.

### DL-#1724 — Phone access and push notifications

- **State:** in_review
- **Owner:** claude
- **Issue:** #1724
- **Branch:** `feat/ux-overhaul`
- **PR:** see branch
- **Paths:** `backend/push.py`, `backend/webpush_crypto.py`, `frontend/public/sw.js`, `frontend/src/pages/PushSettings.tsx`, `frontend/src/index.css` (`.push-settings*`), `frontend/src/pages/StaffConsole/Mobile.tsx`, `frontend/src/pages/StaffConsole/mobile.css`, `docs/runbooks/phone-access-tailnet.md`, `tests/frontend/test_color_literal_budget.py`
- **Started:** 2026-09-27
- **Last verified:** 2026-09-27 (2026-09-27: push pytest 22 passed; tests/frontend guards 37 passed on Windows; StaffConsole vitest 221 passed; tsc + eslint clean)
- **Summary:** Self-hosted Web Push (RFC 8291/8292, VAPID JWT 12h); tailnet phone runbook; PushSettings uses scoped .push-settings\_\_\* classes and badge tokens (no inline styles); mobile roster honours desktop tiering; composer clears shell nav; chevron back button; empty-thread hint. rgba budget guard counts usages only (84 -> 61).
- **Next step:** Ship in the consolidated UX PR for epic #1718.

### DL-#1719 — Neutral design tokens, type scale and flat cards

- **State:** in_review
- **Owner:** claude
- **Issue:** #1719
- **Branch:** `feat/ux-overhaul`
- **PR:** see branch
- **Paths:** `frontend/src/design/tokens.ts`, `frontend/src/design/fleetThemes.ts`, `frontend/src/index.css` (tokens, globals, `.glass-card`, `.button`, `.filter-pill`), `tests/test_frontend_integrity.py` (palette contract), `docs/mobile-design-system.md`
- **Started:** 2026-09-27
- **Last verified:** 2026-09-27 (2026-09-27 after rebase on 19e4f920: vitest 187/188 files (CodeRequests load flake, 19/19 alone); pytest staff+api+unit+integrity 2290 passed; tsc + eslint clean)
- **Summary:** Neutral zinc palette (#111113 base), 4.5:1 muted text on every surface, flat .glass-card with opt-in hover; the mobile token contract test and design-system doc now pin the new palette.
- **Next step:** Ship in the consolidated UX PR for epic #1718.

### DL-#1722 — Console-first Staff page with an Attention drawer

- **State:** in_review
- **Owner:** claude
- **Issue:** #1722
- **Branch:** `feat/ux-overhaul`
- **PR:** see branch
- **Paths:** `frontend/src/pages/Staff/{StaffPage,InboxPanel,Board,Holds,Roster,OutcomesTable}.tsx`, `InboxPanel.css`, `Board.css`, `StaffPage.css`, `frontend/src/primitives/focusable.ts`
- **Started:** 2026-09-27
- **Last verified:** 2026-09-27 (Staff vitest 31 files / 233 passed; tsc and eslint clean; 1440x900 browser check of the redesigned drawer against the live hub)
- **Summary:** The 8,000px inbox becomes a one-line "Waiting on you" bar with per-kind counts and a focus-trapped drawer styled as a modern inbox (underline tabs with counts, borderless rows with tinted kind icons, severity named only when high or critical, section headers for grouped sign-ins and decisions); the Board is a one-line strip with quotas, late roles and machines behind a disclosure; emoji replaced by SVG icons.
- **Next step:** Ship in the consolidated UX PR for epic #1718.

### DL-#1725 — Inbox reports fewer, grouped items

- **State:** in_review
- **Owner:** claude
- **Issue:** #1725
- **Branch:** `feat/ux-overhaul`
- **PR:** see branch
- **Paths:** `backend/staff/inbox.py`, `tests/staff/test_inbox_*.py`, `tests/api/test_staff_inbox.py`, `frontend/src/pages/Staff/{InboxPanel.tsx,InboxPanel.css,inboxTypes.ts}`
- **Started:** 2026-09-27
- **Last verified:** 2026-09-27 (pytest tests/api/test_staff_inbox.py tests/staff 328 passed; ruff, ruff format and mypy clean on inbox.py; Staff vitest 31 files / 233 passed)
- **Summary:** Display-only: one "N providers need sign-in" item (medium; high only when no provider for a dispatchable role is signed in), identical pending proposals collapse into one item, project decisions become one low-severity item per repo, and items sort by severity, approvals first, newest first. `counts` keep per-provider and per-decision totals. The drawer shows up to five detail lines per grouped item.
- **Next step:** Ship in the consolidated UX PR for epic #1718.

### DL-#1721 — Staff Console roster and context pane restyled

- **State:** in_review
- **Owner:** claude
- **Issue:** #1721
- **Branch:** `feat/ux-overhaul`
- **PR:** see branch
- **Paths:** `frontend/src/pages/StaffConsole/{Roster,RosterGroup,RosterRow,ContextPane}.tsx`, `roster.css`, `context.css`, `rosterUtils.ts`
- **Started:** 2026-09-27
- **Last verified:** 2026-09-27 (StaffConsole vitest 27 files / 214 passed; browser check: status dot pinned to the avatar corner)
- **Summary:** Roster rows get tinted avatars, status dots and a full-reason tooltip; held roles read "N standing rules" instead of the raw hold text (guardrail vs schedule split tracked in #1726); groups collapse and persist; the context pane gets skeleton loading and token-only styling.
- **Next step:** Ship in the consolidated UX PR for epic #1718.

### DL-#1720 — Staff Console thread and composer restyled

- **State:** in_review
- **Owner:** claude
- **Issue:** #1720
- **Branch:** `feat/ux-overhaul`
- **PR:** see branch
- **Paths:** `frontend/src/pages/StaffConsole/{Desktop,Thread,MessageItem,Composer,threadMarkdown}.tsx`, `cards/**`, `thread.css`, `threadMarkdown.css`, `composer.css`, `desktop.css`
- **Started:** 2026-09-27
- **Last verified:** 2026-09-27 (StaffConsole vitest 27 files / 214 passed; tsc and eslint clean)
- **Summary:** Chat-style thread (user bubbles right, assistant prose left, 15px prose type), the composer pinned to the bottom of the console, cards restyled with inline styles moved to CSS; Approve/Deny gating unchanged; accent-text colours use `--text-on-accent`.
- **Next step:** Ship in the consolidated UX PR for epic #1718.

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

## Shipped

Shipped entries move to `DEVELOPMENT_LOG_ARCHIVE_<year>.md` (the validator caps this file at 100 kB).

## Archive

Older entries live in `DEVELOPMENT_LOG_ARCHIVE_<year>.md`.
