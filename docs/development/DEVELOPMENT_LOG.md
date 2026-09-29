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

### DL-#1797 — follow-up retries launch through retry.py (BR-03)

- **State:** in_review
- **Owner:** claude
- **Issue:** #1797 (Board 2026-09-29, RD review PR #1776 item BR-03; depends on BR-01 #1795 and BR-02 #1796)
- **Branch:** `fix/followup-retry-launch-1797` from `origin/main` (`6458d3c`, BR-01 and BR-02 merged)
- **PR:** #1818
- **Paths:** `backend/staff/retry.py`, `backend/staff/followup.py`, `tests/api/test_staff_retry_launch.py`, `tests/api/test_staff_followup.py`
- **Started:** 2026-09-29
- **Last verified:** 2026-09-29 @ `1b84dff` (GREEN: 9 retry-launch tests, 30 followup and retry tests; 1208 passed in the staff/retry/followup/dispatch/runner/idempotency selection of `tests/api` and `tests/unit`; ruff clean)
- **Summary:** The follow-up engine wrote a `queued` retry row and linked it, but no worker consumed it; the scheduler then treated it as active work. `launch_retry(runner, failed, source, holds, allow_classes)` re-reads the run and applies `should_retry` (class, attempts, budget) and the schedule holds. It inserts `{root}-a{n}` with the original's thread, work-item and origin links, then starts the worker through `runner.launch`. A second claimant gets `already claimed`. The follow-up engine takes injectable `runner`/`holds`, cancels and fails a stalled original first, allows the `stalled` class on that path only, links the attempt, records `retry_pending` when another path owns it, and escalates with the refusal reason otherwise. `handle_post_execution_retry` uses the same deterministic ids and now copies provenance.
- **Next step:** Merge PR #1818; then rebase BR-04 (#1798) onto main and open its PR.

### DL-#1804 — Staff Console role readiness and context from real data (BR-11)

- **State:** in_progress
- **Owner:** claude
- **Issue:** #1804 (Board 2026-09-29, RD review PR #1776 item BR-11; BR-09 #1802 capability contract not built)
- **Branch:** `fix/role-readiness-1804` from `origin/main` (`6458d3ca`)
- **PR:** not created
- **Paths:** `frontend/src/pages/StaffConsole/` (`roleContextApi.ts`, `roleDetail.ts`, `useRoleContext.ts`, `ContextReadiness.tsx`, `MobileContextDrawer.tsx`, `ContextPane.tsx`, `Desktop.tsx`, `Mobile.tsx`, `RosterRow.tsx`, `rosterUtils.ts`, `useStaffConsole.ts`, `contextTypes.ts`, `types.ts`, CSS), `frontend/src/hooks/useStaffQueries.ts`, `frontend/src/pages/StaffConsole/__tests__/roleReadiness.test.tsx`
- **Started:** 2026-09-29
- **Last verified:** 2026-09-29 @ `6458d3ca` + working tree (RED: 9/9 new mounted-path tests failed on base; GREEN: 359 passed across `frontend/src/pages/StaffConsole`, `frontend/src/pages/Staff` and `frontend/src/hooks`; `npm run typecheck` and changed-file ESLint clean)
- **Summary:** The roster and context pane show only readiness the backend reported. Provider installation, schedule enabled/hold/next fire, board offline nodes, role runs and work items, and thread-linked runs and work items load through the staff React Query layer from existing routes. Roles read Held, Unavailable (no provider installed), Status unknown or Idle (installed provider) distinctly; loading or a failed source is never shown as ready. The schedule switch persists via `PUT /roles/{role}/schedule` with rollback, or is disabled with its reason. Sign-in state is not reported by the backend and awaits BR-09.
- **Next step:** Push `fix/role-readiness-1804` and open the PR.

### DL-#1819 — regenerate the stale OpenAPI snapshot on main

- **State:** in_review
- **Owner:** claude
- **Issue:** #1819
- **Branch:** `fix/openapi-snapshot-regen` from `origin/main` (`6458d3c`)
- **PR:** not created
- **Paths:** `frontend/src/lib/openapi.json`, `frontend/src/lib/api-types.ts`
- **Started:** 2026-09-29
- **Last verified:** 2026-09-29 @ `6458d3c` (`npm run generate-api:check` passes after regeneration; `npm run typecheck` clean)
- **Summary:** `Frontend Tests / TypeScript typecheck (tsc)` failed on every push to main since #1812, because #1812, #1814 and #1817 changed backend models and routes without regenerating the snapshot, and the PR frontend scope skipped `generate-api:check` for backend-only diffs. This regenerates the snapshot and types only. Running the check on backend-only PRs is the follow-up named in #1819 and needs a workflow change, which ships alone.
- **Next step:** Open the PR, mark it ready and arm auto-merge.

### DL-#1796 — dispatch admission and audit before the worker starts (BR-02)

- **State:** shipped
- **Owner:** claude
- **Issue:** #1796 (Board 2026-09-29, RD review PR #1776 item BR-02; depends on BR-01 #1795)
- **Branch:** `fix/dispatch-admission-audit-1796` from `origin/main` (`1877c6c`, BR-01 merged)
- **PR:** #1817
- **Paths:** `backend/staff/dispatch_service.py`, `backend/staff/runner.py`, `backend/staff/runner_ops.py`, `backend/routers/staff.py`, `backend/routers/staff_v1.py`, `tests/api/test_staff_dispatch_admission.py`
- **Started:** 2026-09-29
- **Last verified:** 2026-09-29 @ `301f7af` (GREEN: 6 admission tests; 1213 passed in the staff/idempotency/dispatch/runner/audit selection of `tests/api` and `tests/unit`; ruff clean)
- **Summary:** `dispatch_staff_run` called `runner.submit()` (row plus daemon worker) and then wrote the fail-closed audit, so an audit failure came after the worker had started. The audit (outcome `admitted`) now comes first, then `runner.admit()` persists the queued row, then `runner.launch()` starts the worker. An `AuditError` becomes 503 `audit_unavailable` with nothing admitted. The run id derives from the command's operation id (`op-X` → `run-X`), so a retry finds the admitted row and does not launch again. The v1 dispatch now passes its reservation id and may re-take a lapsed reservation. Peers forward the id; a non-peer's body cannot set it. A launch error marks the run `failed`/`launch_failed` and returns 503 with the run id. The scheduler, review and run-link paths still use `submit`. Starting an admitted-but-never-launched run after a restart is left to the existing orphan reconcile.
- **Next step:** None; merged as PR #1817 (`6458d3c`).

### DL-#1795 — atomic idempotency reservation for Staff API v1 (BR-01)

- **State:** shipped
- **Owner:** claude
- **Issue:** #1795 (Board 2026-09-29, RD review PR #1776 item BR-01)
- **Branch:** `fix/idempotent-reservation-1795` from `origin/main` (`8169046`)
- **PR:** #1814
- **Paths:** `backend/staff/idempotency.py`, `backend/routers/staff_v1.py`, `backend/routers/staff_threads.py`, `backend/staff/thread_helpers.py`, `tests/unit/test_idempotency_reserve.py`, `tests/api/test_staff_v1_idempotency.py`, `tests/api/test_staff_threads_api.py`
- **Started:** 2026-09-29
- **Last verified:** 2026-09-29 @ `d4229a6` (GREEN: 9 ledger unit tests, 6 route tests and the thread-create test pass; 1096 passed in the staff/idempotency selection of `tests/api` and `tests/unit`; ruff clean)
- **Summary:** The ledger was check-then-act: `get()` before the action and `save()` after, so two same-key requests both ran (the review observed a count of 2). `IdempotencyStore.reserve()` now inserts a pending row under `BEGIN IMMEDIATE` with a payload fingerprint, a lease and an operation id, then returns acquired, replay, in_progress, mismatch or unknown_outcome. `complete()` stores the receipt and `release()` frees a pending key when the action fails. Old tables gain the new columns on open, and their rows replay as before. The v1 handler maps each state to a 409 code. When the receipt cannot be written after the effect, it returns the effect instead of a 503. Cancel, holds and export may re-take a lapsed reservation; dispatch may not. Thread creation takes an optional key. Carrying the operation id through peer forwarding is BR-02 (#1796).
- **Next step:** None; merged as PR #1814 (`1877c6c`).

### DL-#1787 — one route for agents to queue suggestions and draft PRs for the Board

- **State:** in_review
- **Owner:** claude
- **Issue:** #1787
- **Branch:** `feat/board-queue-route-1787`
- **PR:** #1812
- **Paths:** `backend/proposals/`, `backend/staff/board_queue.py`, `backend/staff/groups.py`, `backend/staff/action_executors.py`, `clients/fleet/fleet_client.py`, `clients/fleet/fleet_tools.py`
- **Started:** 2026-09-29
- **Last verified:** 2026-09-29 (RED: new tests failed on main; GREEN: 398 passed, 1 skipped across `tests/clients`, the proposals store/routes, board-queue, board-convene, staff groups/actions/reply-contract, owner-request and code-request suites; `ruff check`/`ruff format` clean)
- **Summary:** Proposals carry an optional `pull_request` (`owner/repo#N`) in the form's linked field. `board.convene` with `include_queue` gives the seats every open `board:proposal` item with its own text, seat-only, and asks for one disposition per proposal. Built after the 2026-09-29 Board sessions, where seats given only a summary table guessed item content and misnumbered items.
- **Next step:** Open the PR, merge it, deploy to Desk, and ask Barb to take the open proposals to the Board.

### DL-#1805 — mobile Staff Console tabs sit under the shell's bottom nav (BR-12)

- **State:** in_review
- **Owner:** codex
- **Issue:** #1805
- **Branch:** `bot/luna-runner1813-20260929`
- **PR:** #1813
- **Paths:** `SPEC.md`, `docs/development/HANDOFF.md`, `docs/development/DEVELOPMENT_LOG.md`, `frontend/src/pages/StaffConsole/Mobile.tsx`, `frontend/src/pages/StaffConsole/mobile.css`, `tests/e2e/mobile.spec.ts`
- **Started:** 2026-09-29
- **Last verified:** 2026-09-29 (Original author validation (Claude): RED: the new `tests/e2e/mobile.spec.ts` #1805 test fails on main's CSS, "inbox tab at 320px"; GREEN: it and SC-D9 pass on all four mobile profiles against a local Vite preview; `vitest run frontend/src/pages/StaffConsole` 246 passed. Desktop CI follow-up: RED: `npm run test:e2e -- --project=chromium-desktop tests/e2e/mobile.spec.ts --grep '#1805'` reported 1 skipped because the regression was under the mobile-only suite gate; GREEN: the same desktop project with `--workers=1` passed 1/1 with no skips, exercising 320/390/430px tab and composer hit targets against local Vite. Thread list/detail responses are mocked so the test is self-contained.) Acceptance follow-up (Codex): RED: the 160px effective-layout check exposed overlapping header controls and an Approve action outside the hit-testable area; the restored heading contract then reproduced a collapsed/overlapping h1 at 160/215px. GREEN: the final focused desktop #1805 run passed 11/11 with one worker and zero retries, including shell tab hit/click at 160/195/215px, header geometry plus 44px controls and visible h1 at 160/195/215/320/390/430px, scroll/hit/click Approve at those widths, and the retained 320/390/430px Inbox/send/approval/focus flows. SC-D8/SC-D9 passed 2/2 on iPhone 12 emulation. Changed-file ESLint, `npm run typecheck`, and targeted mobile spec TypeScript passed. The separate PyJWT/tzdata dependency patch was merged to main by PR #1816 (`8169046d`); its recorded pip-audit runs and `uv lock --check` passed, and its unchanged auth modules were not rerun. The viewport cases resize CSS layout; browser zoom/native keyboard/physical-device safe-area and screen-reader behavior remain unverified.)
- **Summary:** `.staff-mobile` had `min-height: 100vh` inside `.mobile-shell__content`, whose box already stops above the fixed 64px nav, so the console overran it and the nav covered the Console/Inbox/Runs tabs (hit-testing the Inbox tab returned the shell's Staff button). Inside the shell the console is now `height: 100%; min-height: 0`, and the #1724 composer offset, which would now double-count the nav, is removed. The conversation message pane can shrink and scroll within its semantic thread/message wrappers. Header groups stay bounded by the header and wrap naturally when needed; the title remains visible with ellipsis and action targets retain 44px hit areas. #1805 verifies shell-reserved navigation geometry and real tab clicks, plus Inbox counts/filter, send, Approve, selected-thread URL and focus. The PyJWT 2.14.0/tzdata 2026.4 dependency fix is already on `main` through PR #1816; PR #1813 contains no independent dependency change.
- **Next step:** Verify actual 200% browser zoom, native keyboard behavior, phone safe areas and screen-reader navigation on a physical phone before release.

### DL-#1815 — PyJWT 2.14.0 for CVE-2026-102274

- **State:** shipped
- **Owner:** claude
- **Issue:** #1815
- **Branch:** `fix/pyjwt-cve-bump` from `origin/main` (`d4229a6`)
- **PR:** #1816 (merged to `main` at `8169046d11b4474fc9fc45b625e49e9801dd595e`)
- **Paths:** `pyproject.toml`, `uv.lock`, `requirements.lock.txt`, `requirements.txt`
- **Started:** 2026-09-29
- **Last verified:** 2026-09-29 @ `d4229a6` (`uv lock --upgrade-package pyjwt` resolved 78 packages; exports regenerated with the commands in their headers); merged to `main` by PR #1816 at `8169046d11b4474fc9fc45b625e49e9801dd595e`.
- **Summary:** pip-audit flags PyJWT 2.13.0 (CVE-2026-102274, fixed in 2.14.0), which failed lint, security-scan and tests on every PR. The pin moves to 2.14.0. The hashed lock also gains `tzdata==2026.4`, a direct dependency since #1736 that the lock had not been re-exported for.
- **Next step:** Merged by PR #1816; the blocked PRs (#1812, #1814) can re-run against `main`.

### DL-#1793 — Staff Console e2e: cards to act on come from a chat-only requester

- **State:** in_review
- **Owner:** claude
- **Issue:** #1793
- **Branch:** `fix/staff-e2e-requester`
- **PR:** not created
- **Paths:** `tests/e2e/fakes/identity.json`, `tests/e2e/fakes/start_staff_backend.py`, `tests/e2e/staff/identity.ts`, `tests/e2e/staff/fixtures.ts`, `tests/e2e/staff/staff-console.spec.ts`
- **Started:** 2026-09-29
- **Last verified:** 2026-09-29 (`npx playwright test -c tests/e2e/staff/playwright.config.ts` gave 16 passed, same files as closed #1791 head `55a99df`)
- **Summary:** After #1788 an approver's own proposal runs at once, so e2e tests that click Approve/Deny found no button. An `e2e-requester` principal (viewer + `staff.chat`) proposes those cards. A new test checks that the operator's own request runs without a tap, and the run tests start from the operator's request.
- **Next step:** Open the PR for #1793.

### DL-#1789 — approval policy expands role presets so preset-granted approvers can execute

- **State:** in_review
- **Owner:** claude
- **Issue:** #1789
- **Branch:** `bot/issue-1789-approval-role-presets`
- **PR:** not created
- **Paths:** `backend/staff/actions.py`, `tests/unit/test_staff_actions.py`, `tests/api/test_staff_proposal_hardening.py`
- **Started:** 2026-09-29
- **Last verified:** 2026-09-29 (commit `7834b16` on this branch; RED: the preset tests failed on main — the unit policy test raised `PermissionError: Principal '__loopback__' lacks 'staff.approve' scope` and the `/execute` route test 403'd. GREEN: 75 passed on `tests/unit/test_staff_actions.py` + the staff proposal-hardening/proposals/owner-request/tailnet/board-convene API subset, 575 passed on the staff suite subset; `ruff check`/`ruff format --check` clean; CI mypy commands clean)
- **Summary:** `check_approval_policy` now grants `staff.approve` with `principal_has_scope`, the same preset-expanding helper as the action `required_scope` check below it, so principals decide-capable through their role preset (`loopback`, `tailnet-approver`, `operator`) can execute approved proposals from Desk. Presets that do not grant the scope (`bot`, `viewer`) still fail closed, and the 403 error text is unchanged.
- **Next step:** Open the PR for #1789.

### DL-#1792 — split over-cap source files to unblock the main 500-line gate

- **State:** in_review
- **Owner:** claude
- **Issue:** none (main-branch CI red; context RD#1785 steward note)
- **Branch:** `bot/main-red-groups-cap` from `origin/main` (`64a73b4`)
- **PR:** #1792
- **Paths:** `backend/staff/groups.py`, `backend/staff/group_threads.py`, `backend/staff/models.py`, `backend/staff/models_insights.py`, `backend/staff/inbox.py`, `backend/staff/inbox_models.py`, `backend/staff/inbox_auth.py`, `backend/push.py`, `backend/push_store.py`, `frontend/src/pages/StaffConsole/Mobile.tsx`, `MobileRuns.tsx`, `frontend/src/pages/Staff/InboxPanel.tsx`, `inboxIcons.tsx`, `inboxPanelMeta.ts`, touched routers/tests importing the moved symbols
- **Started:** 2026-09-29
- **Last verified:** 2026-09-29 @ `491e09c` (GREEN: gate simulation of the ci-standard find/EXEMPT pipeline passes; `ruff check backend tests` and `mypy backend` clean; 221 pytest passed across affected suites; 139 vitest passed; tsc + eslint clean)
- **Summary:** ci-health-check on main fails "Verify no source file exceeds 500 lines" since 2026-09-27 (CI Standard run 36599799561), red on six files: groups.py 567, models.py 502, inbox.py 613, push.py 519, Mobile.tsx 634, InboxPanel.tsx 646. The gate has only a hard-coded exempt list (no waiver/expiry mechanism), so each file lost a cohesive section to a new module with consumers migrated; no behavior change.
- **Next step:** merge PR #1792 and confirm the next CI Standard run on main is green.

### DL-#1786 — Barb as the front door: owner requests run without a second approval

- **State:** in_review
- **Owner:** claude
- **Issue:** #1786
- **Branch:** `feat/barb-front-door`
- **PR:** #1788
- **Paths:** `backend/identity.py`, `backend/tailnet_identity.py`, `backend/staff/owner_requests.py`, `backend/staff/chat.py`, `backend/routers/staff_threads.py`, `tests/api/test_staff_owner_requests.py`, `tests/api/test_tailnet_identity.py`
- **Started:** 2026-09-29
- **Last verified:** 2026-09-29 (RED: the owner-convene test left the proposal `proposed`, and the three loopback-approve tests failed. GREEN: `tests/api/test_staff_owner_requests.py` + `tests/api/test_tailnet_identity.py` 26 passed)
- **Summary:** `staff.approve` joins `LOOPBACK_SCOPES`. The message route passes the caller into the chat turn as `requester`; after each `post_proposal`, `staff/owner_requests.run_for_requester` executes the proposal off-loop under the requester when `request_approves` holds (requester has `staff.approve`, action risk ≤ MEDIUM) and refreshes the card. The normal role-permission and approval-policy checks still run.
- **Next step:** Open the PR for #1786.

### DL-#1783 — desktop Staff Console keeps `?thread=` in sync

- **State:** in_review
- **Owner:** claude
- **Issue:** #1783
- **Branch:** `fix/staff-console-thread-url-1783`
- **PR:** not created
- **Paths:** `frontend/src/pages/StaffConsole/Desktop.tsx`, `frontend/src/pages/StaffConsole/__tests__/Desktop.test.tsx`
- **Started:** 2026-09-29
- **Last verified:** 2026-09-29 (RED: new Desktop test failed on main. Then `npx vitest run frontend/src/pages/StaffConsole frontend/src/pages/Staff` gave 271 passed; typecheck and eslint clean)
- **Summary:** An effect in `StaffConsoleDesktop` calls `history.replaceState` to set `?thread=<active id>` and keeps other params, so a reload or copied link reopens the open conversation.
- **Next step:** Open the PR for #1783.

### DL-#1781 — staff chat parses `Repo#N` references and binds bare `#N` to the nearest repo

- **State:** in_review
- **Owner:** claude
- **Issue:** #1781
- **Branch:** `fix/staff-repo-hash-refs-1781`
- **PR:** not created
- **Paths:** `backend/staff/chat_issue_context.py`, `tests/unit/test_staff_chat_issue_context.py`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (RED: 2 new parse tests failed on main. Then 22 context tests passed, and 148 passed on `pytest tests/unit/test_staff_chat_issue_*.py tests/unit/test_staff_groups.py tests/api -k "issue or group or board or chat"`)
- **Summary:** `parse_issue_refs` now recognises `Repo#N` for known repo names (masked before the bare pass), binds each bare `#N` to the nearest preceding repo mention (falling back to the first mention), and returns refs in text order.
- **Next step:** Open the PR for #1781.

### DL-#1775 — landing composer sends via auto-route thread; New conversation

- **State:** in_review
- **Owner:** claude
- **Issue:** #1775
- **Branch:** `fix/console-convene-warnings-20260928`
- **PR:** not created
- **Paths:** `frontend/src/pages/StaffConsole/consoleThreads.ts`, `frontend/src/pages/StaffConsole/useStaffConsole.ts`, `frontend/src/pages/StaffConsole/Desktop.tsx`, `frontend/src/pages/StaffConsole/Mobile.tsx`, `frontend/src/pages/StaffConsole/__tests__/consoleThreads.test.ts`, `frontend/src/pages/StaffConsole/__tests__/useStaffConsole.test.tsx`, `frontend/src/pages/StaffConsole/__tests__/Desktop.test.tsx`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (RED: `listThreads` not called with "barb" (0 calls); `createFreshRoleThread` missing; "New conversation" button not found. Then `npx vitest run frontend/src/pages/StaffConsole` — 245 passed)
- **Summary:** Sending from the landing composer with no open thread now opens (or creates) Barb's auto-route thread and delivers the message there instead of failing. A "New conversation" control in the thread header creates a fresh thread for the current role via `createFreshRoleThread`, bypassing thread reuse.
- **Next step:** Open the PR with #1773 and #1774.

### DL-#1774 — dropped-action warnings surface in the Staff Console

- **State:** in_review
- **Owner:** claude
- **Issue:** #1774
- **Branch:** `fix/console-convene-warnings-20260928`
- **PR:** not created
- **Paths:** `frontend/src/pages/StaffConsole/MessageItem.tsx`, `frontend/src/pages/StaffConsole/thread.css`, `frontend/src/pages/StaffConsole/__tests__/MessageItem.test.tsx`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (RED: `getByRole("note", { name: /action warnings/i })` found no element; then `npx vitest run frontend/src/pages/StaffConsole` — 245 passed)
- **Summary:** A role message whose `meta.warnings` is non-empty now shows a compact `role="note"` notice below the message, one line per warning, prefixed `Action not proposed: ` when the warning says an action was dropped.
- **Next step:** Open the PR with #1773 and #1775.

### DL-#1773 — board-secretary may propose board.convene

- **State:** in_review
- **Owner:** claude
- **Issue:** #1773
- **Branch:** `fix/console-convene-warnings-20260928`
- **PR:** not created
- **Paths:** `backend/staff/actions.py`, `tests/unit/test_staff_actions.py`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (RED `test_board_secretary_may_propose_board_convene`; then 45 passed, 1 skipped across the actions, reply-contract and board.convene API tests)
- **Summary:** The role that chairs the Board could not propose convening it, and its `board.convene` was silently dropped. `check_role_permission` now grants `board.convene` to `BOARD_PROPOSAL_ROLE`; the owner still approves it.
- **Next step:** Open the PR with #1774 and #1775.

### DL-#1768 — Fleet context cold-start timeout budget and startup pre-warming

- **State:** in_review
- **Owner:** antigravity
- **Issue:** #1768
- **Branch:** `fix/1768-fleet-context-cold-start-budget`
- **PR:** #1771
- **Paths:** `backend/staff/chat_fleet_context.py`, `backend/server.py`, `tests/unit/test_staff_chat_fleet_context.py`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (`pytest tests/unit/test_staff_chat_fleet_context.py` — 15 passed; `ruff check`/`ruff format --check` clean)
- **Summary:** First chat turns after a dashboard restart timed out reading peer-node briefing and sessions because no last-good snapshot existed yet and the cold reads exceeded the tight 5.0s steady-state timeout budget. Implemented `COLD_SOURCE_TIMEOUT_SECONDS = 15.0` used whenever a tool has no prior last-good snapshot in `_LAST_GOOD`, ensuring the initial cold fetch succeeds and caches its payload. Also added `warm_fleet_context_snapshots` scheduled in the background during server startup to asynchronously pre-warm all fleet context snapshots.
- **Next step:** Review and merge PR #1771.

### DL-#1354 — Barb orchestration Board review packet

- **State:** in_review
- **Owner:** codex
- **Issue:** #1354 (review deliverable only; epic remains open)
- **Branch:** `docs/barb-orchestration-review-20260928`
  #1776
- **Paths:** `docs/development/BARB_ORCHESTRATION_REVIEW.md`, `docs/development/barb-review-assets/`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (190 focused Python tests and 534 frontend tests passed; four isolated defect reproductions; desktop/mobile inspection; document checks passed; 18 baseline-only SPEC validator failures disclosed)
- **Summary:** Documentation-only implementation/UX review pinned to df2f9093: 18 proposed issue bodies with priorities, evidence, owners, dependencies and acceptance criteria. Runtime, deployment, ingress and standing authority are unchanged.
- **Next step:** Board reviews the packet and decides which draft issues and policy changes to authorize.

### DL-#1770 — Staff approvals from another tailnet device

- **State:** in_review
- **Owner:** claude
- **Issue:** #1770
- **Branch:** `fix/board-seats-context-20260928`
- **PR:** not created
- **Paths:** `backend/tailnet_identity.py`, `backend/identity.py`, `tests/api/test_tailnet_identity.py`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (RED `test_tailnet_signin_from_other_device_can_approve`; then WSL `pytest tests/api/test_tailnet_identity.py tests/api -k "tailnet or scope or loopback or auth or proposal"` gave 194 passed, 2 skipped; ruff and mypy clean)
- **Summary:** No one could approve Staff Console proposals on a loopback/tailnet node, because neither principal had `staff.approve`. A Tailscale sign-in from a tailnet device other than the host now gets the `tailnet-approver` role, which grants only `staff.approve`. The host is identified by `DASHBOARD_TAILSCALE_SELF_IPS`, and the role is refused when that is unset. Loopback, which covers every agent on the host, stays without approval rights.
- **Next step:** Deploy with `DASHBOARD_TAILSCALE_SELF_IPS` set on DeskComputer and approve a MEDIUM proposal from the phone.

### DL-#1767 — Board seats see referenced issue/PR items

- **State:** in_review
- **Owner:** claude
- **Issue:** #1767
- **Branch:** `fix/board-seats-context-20260928`
- **PR:** not created
- **Paths:** `backend/staff/chat_issue_context.py`, `backend/staff/groups.py`, `tests/unit/test_staff_chat_issue_context.py`, `tests/unit/test_staff_groups.py`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (WSL `pytest tests/unit/test_staff_groups.py tests/unit/test_staff_chat_issue_context.py tests/unit/test_staff_chat_issue_prompt.py tests/unit/test_staff_reply_contract.py tests/api/test_staff_board_convene_api.py tests/api -k "group or board or contract or chat" -q` — 167 passed, 1 skipped; `ruff check`/`ruff format --check` clean; `mypy --ignore-missing-imports` clean)
- **Summary:** Board seats never saw the material a `board.convene` question referenced, and a truncated Markdown file's marker didn't say what fell off. New role-independent `build_referenced_items_block()` in `chat_issue_context.py` (delegated to by `build_issue_context_block`) is called by `execute_group_turn()` with a larger Board budget (`BOARD_MD_FILE_CHARS`=60000, `BOARD_BLOCK_CHARS`=80000) before fanning out; seats get `prompt + block`, while `collate_consensus`/`board.propose` keep the original prompt. A truncated Markdown file now lists the `#`/`##`/`###` headings it cut off.
- **Next step:** Open the PR referencing #1767.

### DL-#1766 — Contract action table shows params_schema

- **State:** in_review
- **Owner:** claude
- **Issue:** #1766
- **Branch:** `fix/board-seats-context-20260928`
- **PR:** not created
- **Paths:** `backend/staff/reply_contract.py`, `tests/unit/test_staff_reply_contract.py`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (WSL `pytest tests/unit/test_staff_reply_contract.py -q` — 19 passed, 1 skipped; `ruff check`/`ruff format --check` clean; `mypy --ignore-missing-imports` clean)
- **Summary:** `generate_chat_contract_text()`'s action table never showed `ActionDefinition.params_schema`, so roles guessed param names when proposing actions (Barb invented `agenda`/`seats`/`mode`/`rounds` for `board.convene` instead of using `question`/`title?`). New `_render_params_schema()` renders the schema as `name: type` (optional keys as `name?: type`, empty schema as `—`) in a new `Params` column.
- **Next step:** Open the PR referencing #1766.

### DL-#1764 — Host-neutral OAuth 503 hint and Tailscale identity auth guidance

- **State:** in_progress
- **Owner:** antigravity
- **Issue:** #1764
- **Branch:** `fix/1764-oauth-hint-tailscale-guidance`
- **PR:** not created
- **Paths:** `backend/routers/auth.py`, `tests/api/test_oauth_hint_host_neutral.py`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (`pytest tests/api/test_oauth_hint_host_neutral.py tests/test_oauth_production_readiness.py tests/test_oauth_config.py` 17 passed; `ruff check`/`ruff format --check` clean; `mypy` clean)
- **Summary:** Make GitHub OAuth 503 error hint host-neutral, eliminating the hard-coded "OGLaptop" machine name; dynamically mention DASHBOARD_TAILSCALE_AUTH as the tailnet alternative when Tailscale auth is disabled.
- **Next step:** Commit and create PR referencing #1764.

### DL-#1762 — read_issue chat context and board.convene

- **State:** in_review
- **Owner:** claude
- **Issue:** #1762
- **Branch:** `fix/board-review-flow-20260928` (consolidated)
- **PR:** not created
- **Paths:** `backend/staff/chat_issue_context.py`, `backend/staff/chat.py`, `backend/staff/chat_fleet_context.py`, `backend/staff/groups.py`, `backend/staff/action_executors.py`, `backend/routers/staff_groups.py`, `tests/unit/test_staff_chat_issue_context.py`, `tests/unit/test_staff_chat_issue_prompt.py`, `tests/unit/test_staff_groups.py`, `tests/api/test_staff_board_convene_api.py`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (WSL `pytest tests/unit -k "staff_chat or staff_groups or staff_proposals or staff_action_executors" tests/api/test_staff_groups_api.py tests/api/test_staff_proposals_api.py tests/api/test_staff_board_convene_api.py -q` — 151 passed; `ruff check`/`ruff format --check` clean; `mypy --ignore-missing-imports` clean)
- **Summary:** Barb's role declared the `read_issue` chat tool but it was a documented no-op, so she could not discuss an issue/PR the owner named in chat, and no action took a question to the Board. New `chat_issue_context.py` recognises `owner/repo#N`/GitHub URLs/`PR #N`/bare `#N` (max 3, deduped) and injects bounded title/state/labels/body plus PR changed-files and changed-markdown text, wired into `chat.py` next to the fleet context block. New `board.convene` action (MEDIUM risk, `staff.chat` scope) creates a Board group thread and starts one group turn via a shared `create_group_thread` helper (factored out of the `/groups/{id}/threads` router endpoint, DRY) and a new `convene_board_thread`, scheduled off the proposals worker thread with `loop_bridge.run_on_loop`.
- **Next step:** Open the PR referencing #1762.

### DL-#1761 — Warm the role cache off-loop before gathering fleet context

- **State:** in_review
- **Owner:** claude
- **Issue:** #1761
- **Branch:** `fix/chat-fleet-context-timeouts`
- **PR:** #1765
- **Paths:** `backend/staff/chat_fleet_context.py`, `backend/coordination/briefing.py`, `backend/coordination/staff_view.py`, `backend/staff/store.py`, `backend/staff/conversations.py`, `backend/staff/audit.py`, `backend/staff/work_items.py`, `backend/staff/idempotency.py`, `tests/unit/test_staff_chat_fleet_context.py`, `tests/unit/test_staff_audit.py`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (WSL `pytest tests/unit/test_staff_chat_fleet_context.py tests/unit/test_staff_chat_fleet_prompt.py tests/unit/test_staff_chat_memory.py -q` → 14 passed; `pytest tests/api/test_coordination_claims_auth.py tests/api/test_coordination_api.py tests/clients/ -q` → 177 passed; `ruff check`/`ruff format --check` clean; mypy `--explicit-package-bases` clean. CI fix re-check: `pytest tests/unit/test_staff_audit.py tests/unit/test_conversations_store.py tests/unit/test_staff_inbox_proposals.py tests/unit/test_staff_run_card_relay.py tests/unit/test_staff_router.py tests/unit/test_staff_v1_primitives.py tests/staff/test_inbox_needs_input.py tests/api/test_staff_proposals_api.py tests/unit/test_staff_chat_fleet_context.py` → 69 passed; the new concurrent first-touch regression test is RED before the fix — `sqlite3.OperationalError: database is locked` and `duplicate column name` on a fresh `staff_runs.sqlite3` — and GREEN after)
- **Summary:** `build_fleet_context_block`'s `_run_source` used to await each source's coroutine directly on the shared event loop under a 5 s `asyncio.wait_for`. A source with synchronous blocking work inside its `async def` (traced to `load_roles()`'s directory glob/stat/YAML parse on a cold mtime-cache, reached via `build_staff_summary` → `staff.fleet.local_board`) froze the whole loop, starving every other concurrently gathered source and their timeout timers, so fast sources (0.4 s over HTTP) timed out in lockstep with the slow one. An earlier attempt in this same branch isolated each source on its own private event loop in a worker thread, but that's unsafe here: `backend/gh_client.py` has a module-level shared `httpx.AsyncClient`/`asyncio.Lock` bound to the main loop, and `read_briefing`/`read_sessions` reach GitHub through it via `aggregate_board`/`staff_runs` — driving those from a foreign per-source loop risks `RuntimeError` or corrupting the shared client's pool, a failure fake-source tests can't catch. Replaced with the minimal fix: `build_fleet_context_block` warms `staff.roles.load_roles()`'s mtime-cache via `asyncio.to_thread` once per turn before gathering (failure logged and swallowed), so the later in-loop `load_roles()` call only re-stats cached files instead of a cold full parse; `coordination/briefing.py`'s `_holds()` (a plain sync call on the loop) also now runs via `asyncio.to_thread`. Kept the module-level per-tool last-good snapshot cache: a later timeout/error renders `stale (age Ns): <body>` instead of `unavailable` when the tool has succeeded earlier in the process.
- **CI race fix (PR #1765):** the branch also reads `staff_runs` off the loop (`coordination/staff_view.staff_runs` via `asyncio.to_thread`), which surfaced the latent cross-store SQLite race behind run 36524561683 (`tests/api/test_staff_proposals_api.py` ERROR at setup, `PRAGMA journal_mode=WAL` → `database is locked`): staff store constructors (`RunStore`, `ConversationStore`+nested `StaffAuditStore`, `WorkItemStore`, `IdempotencyStore`) can first-touch the same fresh `staff_runs.sqlite3` from different threads — a leaked staff-run worker lazily building a store (e.g. `handle_run_status_change` → `update_linked_work_item` → `get_work_item_store` on its own thread) while a component in another thread builds one. SQLite returns `SQLITE_BUSY` immediately for `journal_mode=WAL` — without invoking the busy handler — when the conversion races another connection's first touch of the file, and concurrent `ALTER TABLE` migrations are not idempotent either. All five constructors now serialize first-touch init (connect, WAL pragma, schema/migrations) through a new shared per-path reentrant `staff.store.first_touch_lock`, exercised by `tests/unit/test_staff_audit.py::test_concurrent_first_touch_store_init_is_serialized`.
- **Next step:** Merge PR #1765 (`fix/chat-fleet-context-timeouts`) after the `tests (3.11)` matrix is green.

### DL-#1760 — Auto-route pre-router follow-up and handoff display-name fixes

- **State:** in_review
- **Owner:** claude
- **Issue:** #1760
- **Branch:** `fix/board-review-flow-20260928` (consolidated)
- **PR:** not created
- **Paths:** `backend/staff/chat_preroute.py`, `backend/staff/reply_contract.py`, `tests/unit/test_staff_chat_preroute.py`, `tests/api/test_staff_chat_preroute_api.py`, `tests/unit/test_staff_reply_contract.py`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (WSL `pytest tests/api/test_staff_chat_preroute_api.py tests/api/test_staff_routing_api.py tests/unit/test_staff_reply_contract.py tests/unit/test_staff_chat_preroute.py tests/unit/test_staff_chat_handoff.py tests/code_requests/test_handoff_rules_drift.py -q`: 69 passed, 1 skipped; `ruff check`/`ruff format --check` clean; mypy clean)
- **Summary:** A pasted decision table's stray keyword hit pre-routed a caller's reply to Barb away from her mid-conversation, and a role's `handoff: Board Secretary` display name was silently dropped by the single-token handoff regex. `chat_preroute._preroute` now only allows keyword pre-routing (not explicit `/role`/`@mention`) when the auto thread has no prior Barb reply before the caller's message, and strips pasted markdown table rows/blockquotes/fenced code before the keyword stage. `reply_contract._HANDOFF_RE` now accepts a display name and slugifies it (`Board Secretary` -> `board-secretary`); unknown-role rejection in `chat_handoff.py` is unchanged.
- **Next step:** Open a PR referencing #1760.

### DL-#1759 — SPA shell no-cache + retired-role roster status

- **State:** in_review
- **Owner:** claude
- **Issue:** #1759
- **Branch:** `fix/board-review-flow-20260928` (consolidated)
- **PR:** not created
- **Paths:** `backend/server.py`, `tests/test_spa_fallback.py`, `frontend/src/pages/StaffConsole/rosterUtils.ts`, `frontend/src/pages/StaffConsole/types.ts`, `frontend/src/pages/StaffConsole/RosterRow.tsx`, `frontend/src/pages/StaffConsole/__tests__/rosterUtils.test.ts`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (WSL `pytest tests/test_spa_fallback.py tests/test_static_serving.py -q` 14 passed; `npx vitest run frontend/src/pages/StaffConsole` 239 passed; `npm run typecheck` and `eslint` clean; `ruff check`/`ruff format --check` clean; mypy clean)
- **Summary:** After a deploy the browser kept running the stale bundle because the SPA shell (`/`, `/t/:tabId`, `/settings/push`) and `/sw.js` were served with no `Cache-Control`, so a cached `index.html` could keep pointing at a superseded hashed `/assets/*` chunk. `serve_index`, `serve_spa_fallback` and `serve_service_worker` now set `Cache-Control: no-cache`; the hashed `/assets/*` `StaticFiles` mount is unchanged. Separately, `computeRoleStatus` never considered `role.retired`, so a retired role showed as "Idle"; it now checks `retired` right after the invalid check and returns a new `"retired"` status carrying its `retired_reason`, with `RosterRow.tsx` labeling it "Retired".
- **Next step:** Open the PR.

### DL-#1758 — Board propose persists text; open_pr fails honestly

- **State:** in_review
- **Owner:** claude
- **Issue:** #1758
- **Branch:** `fix/board-review-flow-20260928` (consolidated)
- **PR:** not created
- **Paths:** `backend/staff/action_executors.py`, `backend/staff/work_items.py`, `tests/unit/test_staff_actions.py`
- **Started:** 2026-09-28
- **Last verified:** 2026-09-28 (WSL `pytest tests/api/test_staff_groups_api.py tests/unit/test_staff_groups.py tests/unit/test_staff_group_consensus.py tests/unit/test_staff_actions.py tests/api/test_staff_work_items.py -q` all passed; `ruff check`/`ruff format --check` clean; mypy clean)
- **Summary:** `execute_board_propose` silently dropped `params["proposal"]`; `execute_open_pr` recorded a success audit row and returned `opened: True` without ever calling GitHub. Added an additive nullable `description` column to `work_items` (guarded `ALTER TABLE` migration, mirroring `staff/store.py`'s `runs` migration pattern) and wired the proposal text through `execute_board_propose`, returned in the result. `execute_open_pr` now validates params, then always returns `success=False, failure_class="not_implemented"` with no audit row.
- **Next step:** Open the PR as draft.

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
