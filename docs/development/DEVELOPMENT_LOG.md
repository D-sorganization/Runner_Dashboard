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
