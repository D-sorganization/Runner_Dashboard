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

### DL-#1463 · Agent Org Plan: Verify Staff Output, Role Routing, Outcomes API

- **State:** proposed
- **Owner:** claude
- **Issue:** #1463 (Repository_Management#1766)
- **Branch:** `claude/runner-dashboard-roles-gaps-k9i38r`
- **PR:** opened right after this commit
- **Paths:** `docs/plans/2026-09-25-agent-org-implementation-plan.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (docs-only plan; rechecked against `main` `4018454`, where WP-0.1/0.2 have landed)
- **Summary:** Runner_Dashboard half of the agent-org gap analysis: work packages for role-name resolution, board proposals in the inbox, a post-run verification step, `/api/staff/outcomes`, and code-reviewer runtime support, then CR-4..CR-8 role bindings.
- **Next step:** Dispatch #1516 (WP-1.1, `tier:strong`), the first Phase 1 package; #1517 and #1518 follow it.

### DL-#1556 · Staff e2e harness is hermetic

- **State:** in_review
- **Owner:** claude
- **Issue:** #1556
- **Branch:** `fix/1556-hermetic-staff-e2e`
- **PR:** #1570
- **Paths:** `tests/e2e/fakes/start_staff_backend.py`, `backend/staff/inbox.py`, `tests/unit/test_staff_inbox_proposals.py`, `tests/e2e/staff/playwright.config.ts`, `tests/e2e/staff/globalTeardown.ts`, `.gitignore`
- **Started:** 2026-09-26
- **Last verified:** 2026-09-26 (RED: globalTeardown failed on unfixed `backend_env()`, listing real `api.github.com`/tailnet-peer lines; GREEN: `STAFF_E2E_PYTHON="wsl -e ...python" npx playwright test -c tests/e2e/staff/playwright.config.ts --reporter=line` → 12 passed, guard silent; `tests/unit/test_staff_inbox_proposals.py` + inbox/fleet unit tests: 9 passed; ruff check/format clean)
- **Summary:** The staff e2e backend (real FastAPI app, fake provider CLIs) leaked outside the harness: it fanned out to real tailnet peers, called `api.github.com` for board proposals, and (traced further) for `/api/health`'s runner probe and the hosted-runner billing audit. Fixed by pinning fleet-peer discovery to this node (`AUTODERIVE_FLEET_NODES=0`/`FLEET_NODES=""`), a new `STAFF_INBOX_GITHUB_SOURCES=0` switch disabling the GitHub-backed inbox sources, and an unset `GH_TOKEN` so `gh_client` fails locally instead of round-tripping. A `--log-file` CLI arg plus `globalTeardown.ts` now assert the backend log never mentions a real peer or GitHub.
- **Next step:** Watch PR #1570 merge and mark this entry shipped.

### DL-#1542 · WP-1.1 follow-up: non-blocking startup and guarded verification

- **State:** in_review
- **Owner:** antigravity
- **Issue:** #1542
- **Branch:** `agy/issue-1542`
- **PR:** #1569
- **Paths:** `backend/staff/reconcile.py`, `backend/staff/runner.py`, `backend/staff/verification.py`, `tests/staff/test_run_verification.py`, `tests/api/test_staff_runner.py`
- **Started:** 2026-09-26
- **Last verified:** 2026-09-26 (tests/staff/test_run_verification.py and tests/api/test_staff_runner.py 95 passed; broader tests -k "verif or reconcile or runner or scheduler" 618 passed, 3 skipped, 1 xfailed)
- **Summary:** Removed blocking `recheck_runs` subprocess calls from `reconcile_orphaned_runs` on startup so the event loop is never blocked (the scheduler thread already periodically rechecks unverified runs off the loop). Wrapped `verify_and_record` so it never raises, evaluating `opens_pr` lazily inside the guard so exceptions cannot crash the runner worker thread or bypass `handle_run_status_change`.
- **Next step:** Merge PR #1569 once CI is green.

### DL-#1501 · SC-G5-5: Code Requests, Assessments and Projects steward dispatch through the request API

- **State:** in_review
- **Owner:** claude (first cut antigravity)
- **Issue:** #1501
- **Branch:** `agy/issue-1501`
- **PR:** #1560
- **Paths:** `backend/code_requests/dispatch_service.py`, `backend/routers/code_requests.py`, `backend/staff/work_requests.py`, `backend/staff/work_request_dispatch.py`, `backend/staff/work_request_executors.py`, `frontend/src/lib/api-types.ts`, `frontend/src/lib/openapi.json`, `frontend/src/pages/Assessments.tsx`, `frontend/src/pages/AssessmentsPage.tsx`, `frontend/src/pages/CodeRequests.tsx`, `frontend/src/pages/CodeRequestsPage.tsx`, `frontend/src/pages/ProjectsPage.tsx`, `frontend/src/pages/codeRequestsTypes.ts`, `frontend/src/pages/__tests__/`, `tests/code_requests/test_dispatch_service.py`, `tests/api/test_staff_requests_kinds.py`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (backend 258 passed; integrity + code requests 225 passed after rebase; vitest pages 735 passed; tsc, eslint, ruff clean; API client regenerated)
- **Summary:** Code Requests, Assessments and the Projects steward dispatch through `POST /api/v1/staff/requests`. Code-request dispatch has one server-side core (`code_requests/dispatch_service.py`) shared by the legacy route and the request kind: profile defaults, prompt notes, standards injection and the history entry. The Console sends the typed prompt and `standards[]`.
- **Next step:** Merge PR #1560.

### DL-#1547 · Chat proposals render as ActionCards with approve, run card, needs-input and cancel

- **State:** in_progress
- **Owner:** claude
- **Issue:** #1547 (epic #1354; closes #1341 with #1546)
- **Branch:** `feat/1547-proposal-cards`
- **PR:** not created (opened with this commit)
- **Paths:** `backend/staff/proposal_cards.py`, `backend/staff/chat.py`, `backend/staff/groups.py`, `backend/staff/thread_bus.py`, `backend/routers/staff_proposals.py`, `frontend/src/pages/StaffConsole/cards/`, `frontend/src/pages/StaffConsole/useStaffConsole.ts`, `frontend/src/pages/StaffConsole/{Desktop,Mobile,MessageItem}.tsx`, `frontend/src/pages/StaffConsole/threadTypes.ts`, `tests/unit/test_staff_proposal_cards.py`, `tests/api/test_staff_proposals_api.py`, `tests/api/test_staff_chat_turns.py`, `tests/e2e/staff/staff-console.spec.ts`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 at 2e851ac9 plus this branch (backend 23 passed; StaffConsole vitest 141 passed; staff e2e 12 passed; `tsc`, eslint, ruff clean)
- **Summary:** Slice A: each proposed action is an `action_proposal` message holding the card; the proposal points at it, decisions rewrite it, and a refused decision re-enables the card. Slice B: live run cards, needs-input answer, and cancel from the card.
- **Next step:** Publish the `action_result` and `run_card` messages `execute_proposal` adds, and render run status updates on the card.

### DL-#1551 · Staff chat failure card remediation context: preserve most specific classified failure

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1551
- **Branch:** `fix/1551-staff-chat-failure-remediation`
- **PR:** #1565
- **Paths:** `backend/staff/chat.py`, `backend/staff/chat_failures.py`, `backend/staff/conversation_models.py`, `tests/e2e/staff/staff-console.spec.ts`, `tests/unit/test_staff_chat_exhausted_chain.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (pytest tests/unit/test_staff_chat_exhausted_chain.py: 4 passed; full chat suites 22 passed; ruff clean; line count chat.py 472 lines, chat_failures.py 172 lines)
- **Summary:** Preserved the most specific classified failure and remediation across fallback provider chain turns. Added failure specificity ranking (`FAILURE_SPECIFICITY`) and `choose_preferred_chat_failure` in `chat_failures.py`. When a primary provider fails meaningfully (e.g. `auth_expired` with `claude auth login`, or crash `unknown`), subsequent generic or unavailable fallback errors (e.g. `provider_error` / `cli_missing` from `ollama` or `systemctl --user start ollama`) no longer overwrite the root failure or remediation instructions.
- **Next step:** None (shipped in PR #1565).

### DL-#1553 · Remediation bulk actions route each repository's targets to that repository

- **State:** shipped
- **Owner:** claude
- **Issue:** #1553 (follow-up to #1500)
- **Branch:** `fix/1553-bulk-act-per-repo`
- **PR:** #1555
- **Paths:** `backend/staff/work_requests.py`, `backend/staff/work_request_executors.py`, `frontend/src/pages/Remediation/remediationBulkRequest.ts`, `frontend/src/pages/RemediationIssues.tsx`, `frontend/src/pages/RemediationPRs.tsx`, `frontend/src/lib/openapi.json`, their tests
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 at 2e851ac9 plus this branch (staff request kinds, bulk request and frontend integrity tests 99 passed, 1 xfailed; Remediation vitest 89 passed; `tsc`, eslint and ruff clean)
- **Summary:** One request per repository instead of every number under the first item's repo; failed rows stay selected; bulk targets are positive, unique and capped at 100; an all-failed request names each target.
- **Next step:** Shipped.

### DL-#1562 · Wire the Mad-Scientist Staff Role into Routing and the Roster

- **State:** shipped
- **Owner:** claude
- **Issue:** #1562
- **Branch:** `feat/1788-mad-scientist-wiring`
- **PR:** #1562
- **Paths:** `backend/staff/router_models.py`, `tests/staff/routing_eval/dataset.py`, `frontend/src/pages/StaffConsole/rosterUtils.ts`, `frontend/src/pages/StaffConsole/__tests__/rosterUtils.test.ts`
- **Started:** 2026-09-26
- **Last verified:** 2026-09-26 at `f5f027d2` baseline (staff pytest green; StaffConsole vitest 142 passed)
- **Summary:** Barb routes mad-scientist requests by distinctive keywords, and the role sits with the Advisors in the Staff Console roster.
- **Next step:** Shipped.

### DL-#1549 · Remove stale tracked vite.config.js shadowing vite.config.ts

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1549
- **Branch:** `fix/1549-remove-stale-vite-config`
- **PR:** #1561
- **Paths:** `vite.config.js`, `vite.config.d.ts`, `.gitignore`, `tests/e2e/staff/playwright.config.ts`, `tests/frontend/test_vite_config.py`, `tests/test_documentation_freshness.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (pytest tests/frontend/test_vite_config.py tests/test_documentation_freshness.py tests/test_frontend_integrity.py: all passed; no tracked vite.config.js or vite.config.d.ts; gitignore updated; playwright config cleaned up)
- **Summary:** Removed stale compiled artifacts `vite.config.js` and `vite.config.d.ts` from git tracking and ignored them in `.gitignore`. Vite resolves `.js` before `.ts`, causing dev servers and build scripts to silently ignore `vite.config.ts` changes and environment variables like `VITE_BACKEND_URL`. Removed explicit `--config vite.config.ts` flag in Playwright config and added comprehensive regression tests asserting both file absence and backend URL config honoring.
- **Next step:** None (shipped in PR #1561).

### DL-#1550 · Web-vitals POST lacks the CSRF header and gets 403

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1550
- **Branch:** `agy/issue-1550`
- **PR:** #1558
- **Paths:** `frontend/src/lib/webVitals.ts`, `frontend/src/lib/__tests__/webVitals.test.ts`, `frontend/src/main.tsx`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (Vitest webVitals 6 passed, related lib/pages suites 37 passed; npm run lint clean; npx tsc clean; backend auth perimeter 13 passed)
- **Summary:** Extracted web-vitals reporting into `frontend/src/lib/webVitals.ts` using `apiRequest` to include the mandatory `X-Requested-With: XMLHttpRequest` CSRF sentinel header on `POST /api/metrics/web-vitals`. Added unit tests in `webVitals.test.ts` asserting CSRF header presence, body payload structure, and failure resilience.
- **Next step:** None (shipped in PR #1558).

### DL-#1552 · Restore green main: trim backend/staff/chat.py under 500 lines

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1552
- **Branch:** `fix/1552-trim-chat-py`
- **PR:** #1554
- **Paths:** `backend/staff/chat.py`, `backend/staff/chat_failures.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (chat unit tests 19 passed; line count chat.py 493 lines, chat_failures.py 122 lines; ruff clean; full codebase line-cap check clean)
- **Summary:** Extracted `record_chat_failure_if_pending` helper logic from `backend/staff/chat.py` into `backend/staff/chat_failures.py`, reducing `chat.py` from 505 to 493 lines to satisfy the 500-line soft cap enforced by `ci-health-check`.
- **Next step:** None (shipped in PR #1554).

### DL-#1500 · SC-G5-4: Remediation Issues and PRs bulk actions go through the request API

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1500
- **Branch:** `feat/1500-remediation-bulk-requests`
- **PR:** #1545
- **Paths:** `backend/staff/work_requests.py`, `backend/staff/work_request_executors.py`, `backend/staff/work_request_dispatch.py`, `frontend/src/pages/RemediationIssues.tsx`, `frontend/src/pages/RemediationPRs.tsx`, `frontend/src/pages/Remediation/remediationBulkRequest.ts`, `frontend/src/pages/Remediation/__tests__/remediationBulkRequest.test.ts`, `frontend/src/pages/__tests__/RemediationIssues.test.tsx`, `frontend/src/pages/__tests__/RemediationPRs.test.tsx`, `tests/api/test_staff_requests_kinds.py`, `tests/test_remediation_bulk_requests.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (pytest tests/api/test_staff_requests_kinds.py and tests/test_remediation_bulk_requests.py: 18 passed; test_frontend_integrity.py: 72 passed, 1 xfailed; ruff check clean; ruff format clean; all touched/new files <= 500 lines)
- **Summary:** Migrated `RemediationIssues.tsx` and `RemediationPRs.tsx` from legacy `/api/issues/dispatch` and `/api/prs/dispatch` to `POST /api/v1/staff/requests` (kinds `issue.act` and `pr.act` via `submitStaffRequest`). Forwarded multi-target selection, provider, prompt, `force` override and `approved_by` principal into WorkRequest. Created single WorkItem per bulk request enumerating all target numbers. Extracted reusable workflow dispatch helpers to `backend/staff/work_request_dispatch.py` (192 lines) and frontend formatting to `remediationBulkRequest.ts` (133 lines) to satisfy $\le 500$-line limits. Surfaced partial failures per target visibly without losing user input.
- **Next step:** None (shipped in PR #1545).

### DL-#1341 · SC-E: Staff Console e2e suite against fake providers

- **State:** in_review
- **Owner:** claude
- **Issue:** #1341 (epic #1354)
- **Branch:** `test/1341-staff-e2e`
- **PR:** #1544
- **Paths:** `tests/e2e/fakes/`, `tests/e2e/staff/`, `playwright.config.ts`, `.github/workflows/frontend-tests.yml`, `backend/staff/chat.py`, `backend/staff/chat_streaming.py`, `frontend/src/pages/Staff/staffApi.ts`, `frontend/src/pages/StaffConsole/useStaffConsole.ts`, `frontend/src/pages/StaffConsole/useThreadStream.ts`, `tests/unit/test_staff_chat_stream_result.py`, `tests/unit/test_staff_chat_exhausted_chain.py`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 at 7f417623 plus this branch (Staff e2e 8/8 and 16/16 repeated; backend chat tests 77 passed; vitest Staff/StaffConsole/hooks 213 passed; `tsc` and ruff clean)
- **Summary:** Real backend, fake provider CLIs. The suite drives chat, fallback, handoff text, SSE resume, provider failures and send failures in a browser. It fixed four bugs: doubled Claude replies, a reply left pending after the whole chain failed, double-encoded Staff POST bodies, and a Console crash on live SSE frames. Approval and run flows wait on proposals being rendered.
- **Next step:** Merge, then render chat proposals as ActionCards and add the approval, run-card, needs-input and cancel specs.

### DL-#1499 · SC-G5-3: Remediation context buttons open a prefilled request: failed runs, mobile, FAB becomes Ask

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1499
- **Branch:** `feat/1499-remediation-context-request`
- **PR:** #1539
- **Paths:** `frontend/src/pages/Remediation/remediationPrefill.ts`, `frontend/src/pages/Remediation/Mobile.tsx`, `frontend/src/pages/Remediation/ActionSheet.tsx`, `frontend/src/pages/Remediation/InFlightTile.tsx`, `frontend/src/pages/RemediationPage.tsx`, `frontend/src/pages/RemediationTab.tsx`, `frontend/src/shell/MobileShell.tsx`, `frontend/src/shell/AskSheet.tsx`, `frontend/src/shell/routing.ts`, `frontend/src/shell/navRegistryData.ts`, `frontend/src/shell/RoutedShell.tsx`, `frontend/src/pages/Staff/StaffPage.tsx`, `frontend/src/pages/Staff/AdvancedDispatchForm.tsx`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (all Vitest suites passed; `npm run lint` clean; `npm run typecheck` clean; pytest passed; ruff check clean; mypy backend clean; all files <= 500 lines)
- **Summary:** Replaced remediation dispatch code on run surfaces (`RemediationPage`, `RemediationTab`, `Remediation/Mobile`, `ActionSheet`) with "Fix this failed run" context button prefilling Staff Console composer / Advanced form with kind `ci.remediate`, target repo, run_id, branch, workflow name, and log excerpt. Converted mobile FAB (`MobileShell.tsx`) to Ask (`AskSheet.tsx`) submitting `staff.dispatch`. Preserved provider/model choices and track in-flight work item ID on `InFlightTile`. Retired `AgentDispatch.tsx`, its nav entry and routes, adding redirects to Staff Console (`/`).
- **Next step:** None (shipped in PR #1539).

### DL-#1540 · Board consensus reports the seats' positions, not canned approval

- **State:** in_review
- **Owner:** claude
- **Issue:** #1540 (SC-B9 follow-up, epic #1354)
- **Branch:** `fix/1540-board-consensus`
- **PR:** #1541
- **Paths:** `backend/staff/groups.py`, `tests/unit/test_staff_group_consensus.py`, `tests/unit/test_staff_groups.py`, `tests/api/test_staff_groups_api.py`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 at a52a0702 plus this branch (`tests/staff`, group unit and API tests, `test_staff_actions.py`: 279 passed; ruff and mypy clean)
- **Summary:** `collate_consensus` no longer asserts agreement. The summary lists each answering seat's position (one line, 240 chars) and keeps the full replies in the seat block. With no answers it reports no quorum and creates no proposal. `board.propose` params are the title, question and positions only; the executor never read the removed repo, urgency and cost fields.
- **Next step:** Merge, then have the coordinator role write a real synthesis over the positions.

### DL-#1342 · SC-D7: Board group thread UI

- **State:** in_review
- **Owner:** claude (reworked from an antigravity draft)
- **Issue:** #1342 (SC-D, epic #1350)
- **Branch:** `agy/issue-1342`
- **PR:** #1537
- **Paths:** `frontend/src/pages/StaffConsole/groupTurn.ts`, `frontend/src/pages/StaffConsole/GroupDeliberationCard.tsx`, `frontend/src/pages/StaffConsole/GroupCostConfirm.tsx`, `frontend/src/pages/StaffConsole/useGroupCostGuard.ts`, `frontend/src/pages/StaffConsole/groupTurn.css`, `frontend/src/pages/StaffConsole/MessageItem.tsx`, `frontend/src/pages/StaffConsole/useStaffConsole.ts`, `frontend/src/pages/StaffConsole/Composer.tsx`, `frontend/src/pages/StaffConsole/Desktop.tsx`, `frontend/src/pages/StaffConsole/Mobile.tsx`, `frontend/src/pages/StaffConsole/threadTypes.ts`, `frontend/src/pages/Staff/staffApi.ts`, `frontend/src/pages/StaffConsole/__tests__/`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 at 7dc5df00 plus this branch (vitest StaffConsole + pages 67 files / 523 passed; `tsc` and eslint clean; frontend static pytest 186 passed)
- **Summary:** A finished group turn (`meta.is_group_turn` with `seat_replies`) renders as `GroupDeliberationCard`: coordinator summary without the duplicated seat block, collapsed seat replies, silent seats marked with their error, and a Board Proposal form (#1284) prefilled only with the seats' replies. `useGroupCostGuard` asks the backend for its estimate before a group send and holds the Composer's send until the user confirms or cancels. The backend guard stays authoritative. The backend summary itself is canned text (#1540).
- **Next step:** Merge, then fix the canned Board consensus in #1540.
### DL-#1516 · WP-1.1: post-run verification of staff runs (report mode)

- **State:** in_review
- **Owner:** claude
- **Issue:** #1516
- **Branch:** `feat/1516-run-verification`
- **Paths:** `backend/staff/verification.py`, `backend/staff/store.py`, `backend/staff/runner.py`, `backend/staff/roles.py`, `backend/staff/scheduler.py`, `backend/staff/reconcile.py`, `backend/staff/action_executors.py`, `backend/staff/models.py`, `frontend/src/pages/Staff/RunDetail.tsx`, `frontend/src/lib/openapi.json`, `frontend/src/lib/api-types.ts`, `tests/staff/test_run_verification.py`, `tests/api/test_staff_runner.py`, `tests/api/test_staff_dispatch_service.py`, `tests/conftest.py`, `frontend/src/pages/__tests__/StaffRunVerification.test.tsx`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (`tests/staff/test_run_verification.py` 74 passed, RED first; runner report/enforce end-to-end and dispatch-verifier tests pass; vitest RunDetail + Staff 18 passed; `tsc`, ruff and mypy clean)
- **Summary:** A run that exits 0 with `STAFF_RESULT:` is no longer taken at its word: `verification.verify_and_record` checks the PR for its branch and head CI after the runner classifies the run, and records `verification`/`verification_detail`/`pr_number` beside the status. Kept out of the classifier so a GitHub outage can only leave a run `unverified`. Re-checks run from the scheduler loop and startup reconcile, bounded by a 24 h window and 20 runs per pass. `STAFF_VERIFY_MODE=report` is the default; `enforce` exists but stays off until the owner turns it on.
- **Next step:** After merge, deploy to one node in report mode and let the owner review verdicts on real runs before enabling `enforce`.

### DL-#1528 · Tests never hold a real GitHub credential

- **State:** in_review
- **Owner:** claude
- **Issue:** #1528
- **Branch:** `fix/1528-hermetic-github-creds`
- **Paths:** `tests/conftest.py`, `tests/unit/test_github_test_isolation.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (`tests/unit/test_github_test_isolation.py` RED 4/4 with `GH_TOKEN` in the parent env, then GREEN; full suite run with a fake parent `GH_TOKEN`)
- **Summary:** `gh_utils.gh_api_write` reaches GitHub through the httpx client (token or GitHub App env) or the `gh` CLI login; the unit-lane network guard covers neither, so code-request tests created real issues. One autouse fixture removes every credential env var, gives the `gh` CLI an empty `GH_CONFIG_DIR` and clears `gh_client`'s cached token. The junk issues were closed as not planned.
- **Next step:** Merge.

### DL-#1521 · Make staff tests hermetic: no real worktrees or gh

- **State:** in_review
- **Owner:** claude
- **Issue:** #1521
- **Branch:** `fix/1521-hermetic-staff-tests`
- **Paths:** `tests/conftest.py`, `tests/unit/test_staff_test_isolation.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (fixture now honours a test's own `STAFF_REPOS_ROOT`, so the knowledge-pack tests that build a tmp corpus pass; `tests/unit/test_staff_test_isolation.py` clean; `tests/staff tests/api/test_staff*.py tests/unit/test_staff*.py` pass under WSL with `HOME`/`USERNAME` isolated)
- **Summary:** `staff.workspace.repos_roots()` always appended real developer checkout roots after any configured `STAFF_REPOS_ROOT`, so staff tests that submitted a run did real `git worktree add` / `gh` against real checkouts. Added one autouse fixture in `tests/conftest.py` that neutralizes `repos_roots()` to `[]`, isolates `STAFF_WORKTREES_ROOT`/`STAFF_RM_ROOT` under `tmp_path`, and guards `add_worktree()` with a DbC assertion against any target outside `tmp_path`. It also patches the `repos_roots` name that `staff.knowledge_refresh` imports directly.
- **Next step:** Merge. The prepend-vs-replace question for `STAFF_REPOS_ROOT` in production stays with the owner; this PR changes tests only.

### DL-#1498 · SC-G5-2: One Advanced dispatch form

- **State:** in_review
- **Owner:** antigravity (first cut), claude (review rework)
- **Issue:** #1498
- **Branch:** `agy/issue-1498`
- **PR:** #1529
- **Paths:** `frontend/src/pages/Staff/AdvancedDispatchForm.tsx`, `frontend/src/pages/Staff/requestKinds.ts`, `frontend/src/pages/Staff/DispatchPlan.tsx`, `frontend/src/pages/Staff/staffApi.ts`, `frontend/src/pages/Staff/StaffPage.tsx`, `frontend/src/pages/FleetCommand/DispatchPanel.tsx`, `frontend/src/pages/__tests__/AdvancedDispatchForm.test.tsx`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (vitest `frontend/src/pages/__tests__/` 46 files / 383 passed; `tsc -p tsconfig.app.json` clean; eslint `--max-warnings 0` clean)
- **Summary:** One dispatch form for the work-request API; `Staff/Assign.tsx` is gone. Kinds come from `requestKinds.ts`, which lists only what `staff.work_requests.REQUEST_KINDS` accepts (today `staff.dispatch`), so the form never offers a request bound to 422. `approval_required` (202) is shown, not dropped.
- **Next step:** When a later SC-G5-1 slice adds a backend kind, add its row to `requestKinds.ts` with its target fields.

### DL-#1486 · SC-B1-G3: One action vocabulary for chat replies and the action registry

- **State:** in_progress
- **Owner:** antigravity
- **Issue:** #1486
- **Branch:** `feat/1486-one-action-vocabulary`
- **Paths:** `backend/staff/reply_contract.py`, `backend/staff/actions.py`, `backend/staff/action_executors.py`, `backend/staff/maintenance.py`, `tests/unit/test_staff_reply_contract.py`, `tests/unit/test_staff_reply_vocabulary.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (pytest tests/unit/test_staff_reply_*.py 21/21 passed; full staff test suite 763 passed, 12 skipped, 0 failed; ruff check clean; ruff format clean; mypy backend/staff clean in 68 source files; all files <= 500 lines)
- **Summary:** Established `ACTION_REGISTRY` as the single authoritative action vocabulary across `reply_contract.py` and `actions.py`: chat replies proposing registered actions (`staff.dispatch`, `staff.review_pr`, `maintenance.*`, etc.) are recognized and valid; unknown action names are dropped with descriptive warnings while preserving prose; reply-contract prompt text is generated dynamically from `ACTION_REGISTRY` (DRY); registered legacy actions (`notify_user`, `claim_issue`, `open_pr`, `submit_proposal`) with callable executors and permission checks; registered 12 fleet maintenance aliases; pinned that every reply-contract action has a registered, callable executor.
- **Next step:** Push branch, open PR referencing Fixes #1486, arm auto-merge, verify CI passes.

### DL-#1465 · Fix flaky test test_staff_hold_and_unhold_lifecycle hits 'database is locked'

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1465
- **Branch:** `fix/1465-staff-actions-db-lock`
- **PR:** #1470
- **Paths:** `backend/staff/actions.py`, `backend/staff/audit.py`, `backend/staff/conversations.py`, `backend/staff/idempotency.py`, `backend/staff/maintenance.py`, `backend/staff/store.py`, `backend/staff/work_items.py`, `tests/unit/test_staff_actions.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (pytest test_staff_actions 30/30 stress-test runs passed with 0 lock errors; ruff check clean; ruff format clean; all files <= 500 lines)
- **Summary:** Eliminated SQLite database lock contention and test flakes across staff actions and stores: (1) Added `timeout=30.0` and `PRAGMA busy_timeout = 30000;` on all staff SQLite connections (`StaffAuditStore`, `ConversationStore`, `RunStore`, `IdempotencyStore`, `WorkItemStore`, maintenance `_vacuum_db`); (2) Reused conversation store's existing audit store instance across proposal state transitions and action context in `execute_proposal`; (3) Isolated `tests/unit/test_staff_actions.py` by resetting stores and runner and mocking background runner worker thread in `clean_env`.
- **Next step:** None (shipped in PR #1470).

### DL-#1497 · SC-G5-1: Work-request API: every dispatch kind as a registered action with a work item

- **State:** in_progress
- **Owner:** claude (slice A), antigravity (slice B)
- **Issue:** #1497
- **Branch:** `feat/1497-work-request-kinds`
- **Paths:** `backend/staff/work_requests.py`, `backend/staff/work_request_executors.py`, `backend/staff/actions.py`, `tests/api/test_staff_requests_kinds.py`, `frontend/src/pages/Staff/requestKinds.ts`, `frontend/src/pages/Staff/AdvancedDispatchForm.tsx`, `frontend/src/pages/__tests__/AdvancedDispatchForm.test.tsx`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (`pytest tests/api/test_staff_requests_kinds.py tests/api/test_staff_requests_api.py` 24 passed; `ruff check .` clean; `ruff format --check` clean on touched files; `mypy backend` clean in 278 files; all files <= 500 lines)
- **Summary:** Slice B adds all remaining request kinds (`ci.remediate`, `issue.act`, `pr.act`, `code_request.dispatch`, `assessment.run`) to `POST /api/v1/staff/requests`. Registered executors run through `ACTION_REGISTRY` with scoped permissions and risk classifications. Pydantic validation strictly enforces per-kind target and argument constraints (`extra=forbid`). Dry-run previews return the resolved plan without executing. Real execution links the work item to the dispatched workflow run. Frontend `AdvancedDispatchForm` and `requestKinds.ts` support all 6 kinds, omitting `role` when the kind does not take a role.
- **Next step:** Push branch, open PR with `Fixes #1497`, arm auto-merge, watch CI, release lease.

### DL-#1489 · SC-B1-G6: Redact secrets everywhere conversations and runs persist

- **State:** in_review
- **Owner:** claude
- **Issue:** #1489
- **Branch:** `fix/1489-redact-everywhere`
- **Paths:** `backend/staff/redaction.py`, `backend/staff/conversations.py`, `backend/staff/conversation_proposals.py`, `backend/staff/store.py`, `backend/staff/runner.py`, `tests/unit/test_staff_redaction_everywhere.py`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (new table test 15 passed, RED on all 14 boundaries before; ruff clean; `mypy backend/` clean in 253 files)
- **Summary:** `redact_value` (shape-preserving) is applied at every write of thread title/meta, message meta, proposal params/reasons, run text columns, run events and transcript lines.
- **Next step:** Merge the PR, then file a follow-up for audit-log `detail` redaction and multi-line PEM keys in transcripts.

### DL-#1485 · SC-B1-G2: Harden the action-proposal API

- **State:** in_review
- **Owner:** claude
- **Issue:** #1485
- **Branch:** `fix/1485-proposal-hardening`
- **Paths:** `backend/staff/actions.py`, `backend/staff/action_executors.py`, `backend/routers/staff_proposals.py`, `backend/staff/conversation_proposals.py`, `backend/staff/conversation_models.py`, `backend/staff/conversations.py`, `backend/staff/chat.py`, `backend/staff/maintenance_detect.py`, `backend/routers/assistant.py`, `backend/staff/groups.py`, `tests/api/test_staff_proposal_hardening.py`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (hardening suite 11 passed; proposal/maintenance/chat/assistant/client selection 406 passed; ruff clean; `mypy backend/` clean in 250 files)
- **Summary:** Proposal creation needs `staff.chat`, a registered action and a real thread/message; risk comes only from the registry; only approved proposals execute (one-step `approve=True` records the decision after policy checks), failed ones need a retry decision; `required_scope` enforced at execute; no results posted to missing threads.
- **Next step:** After #1483 merges, rebase onto main, retarget the PR to main, mark it ready and arm it.

### DL-#1487 · SC-B1-G4: Shared staff dispatch service

- **State:** in_review
- **Owner:** claude
- **Issue:** #1487
- **Branch:** `fix/1487-shared-dispatch` (stacked on `feat/1448-wire-maintenance`, PR #1483)
- **Paths:** `backend/staff/dispatch_service.py`, `backend/staff/loop_bridge.py`, `backend/staff/action_executors.py`, `backend/staff/maintenance_github.py`, `backend/routers/staff.py`, `tests/api/test_staff_dispatch_service.py`, `tests/staff/routing_eval/test_action_executor_roles.py`, `tests/unit/test_staff_actions.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (new suite 14 passed; staff/dispatch/proposal/action/maintenance/fleet selection green in WSL venv; ruff clean; `mypy backend/` clean in 252 files)
- **Summary:** `/run` and the `staff.dispatch` action share `dispatch_staff_run`: forwarding, rate limit, dry-run and audit apply to approved proposals. Executor runs in the proposal routes' worker thread and reaches the loop via `loop_bridge.run_on_loop`; outside a worker thread it fails as `bridge_unavailable`.
- **Next step:** Merge PR #1510 (on main, armed), then route the #1497 requests API through `dispatch_staff_run`.

### DL-#1285 · CR-4: Planner stage — high-tier agent authors execution-ready issues and turnover docs

- **State:** in_progress
- **Owner:** claude
- **Issue:** #1285 (epic #1279)
- **Branch:** `feat/1285-planner-stage`
- **Paths:** `backend/code_requests/plan.py`, `backend/code_requests/plan_validator.py`, `backend/code_requests/plan_render.py`, `backend/code_requests/handoff_rules.py`, `backend/code_requests/planner.py`, `backend/code_requests/plan_store.py`, `backend/code_requests/plan_filing.py`, `backend/code_requests/plan_service.py`, `backend/code_requests/lifecycle.py`, `backend/routers/code_request_plans.py`, `backend/server.py`, `frontend/src/lib/openapi.json`, `frontend/src/lib/api-types.ts`, `tests/code_requests/test_plan_validator.py`, `tests/code_requests/test_planner_stage.py`, `tests/code_requests/test_handoff_rules_drift.py`, `tests/api/test_code_request_plans_api.py`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (complexity vocabulary aligned to docs/issue-taxonomy.md; code_requests + code-request API + auth perimeter: 93 passed; ruff and mypy clean; API snapshot regenerated, no drift)
- **Summary:** Backend of the planner stage: validated JSON plan contract, dashboard-rendered turnover docs checked against the vendored fleet handoff rules, re-prompt loop (2 retries) then `failed`, approval-gated resumable filing with sub-issue links. The draft view in the Code Request detail UI is the remaining slice.
- **Next step:** Delegate the Code Request detail "Plan" panel (render, inline edit via `PUT .../plan/draft`, approve) to a `tier:cli` agent against the generated `api-types.ts`.

### DL-#1491 · SC-B1-G8: Reconcile chat messages stuck in pending/streaming after a backend restart

- **State:** in_progress
- **Owner:** antigravity
- **Issue:** #1491
- **Branch:** `agy/issue-1491`
- **Paths:** `backend/staff/reconcile.py`, `backend/staff/conversations.py`, `backend/staff/conversation_models.py`, `backend/staff/audit.py`, `tests/unit/test_staff_reconcile.py`, `tests/unit/test_conversations_store.py`, `tests/api/test_staff_threads_api.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (pytest unit & api tests passed 33/33; ruff check & format clean; mypy clean in 4 files)
- **Summary:** Reconcile chat messages stuck in non-terminal delivery states (pending/streaming) across backend restarts. On startup, mark non-terminal reply messages as failed with meta.failure_class='interrupted_by_restart', post a system message offering a retry, and audit every state change under SC-A8 while leaving user messages and complete messages untouched.
- **Next step:** Push branch agy/issue-1491 and open draft PR.

### DL-#1502 · Fix Fleet Orchestration false successes for dispatch and deploy

- **State:** in_progress
- **Owner:** antigravity
- **Issue:** #1502
- **Branch:** `fix/1502-orchestration-false-success`
- **Paths:** `backend/routers/orchestration.py`, `frontend/src/pages/FleetOrchestrationPage.tsx`, `frontend/src/lib/api-types.ts`, `frontend/src/lib/openapi.json`, `tests/api/test_orchestration_dispatch.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (pytest tests/api/test_orchestration_dispatch.py 5/5 passed, ruff clean, mypy clean, scripts/gen-api-client.sh --check clean, all files <= 500 lines)
- **Summary:** Fix false successes in Fleet Orchestration: `/api/fleet/orchestration/dispatch` returns classified 502 `upstream_error` with `dispatched: false` and gh stderr detail when gh fails, injects `machine_target` into workflow dispatch inputs, and `/api/fleet/orchestration/deploy` returns 501 `not_wired` while still recording the audit attempt. Added dedicated API test suite in `tests/api/test_orchestration_dispatch.py`.
- **Next step:** Push branch, open PR with Fixes #1502, enable auto-merge, verify CI passes.

### DL-#1522 · Restore green main: regenerate API contract types and synchronize openapi schema after #1512

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1522
- **Branch:** `fix/1522-api-contract-drift`
- **PR:** #1523
- **Paths:** `frontend/src/lib/api-types.ts`, `frontend/src/lib/openapi.json`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (`scripts/gen-api-client.sh --check` passed cleanly with code 0 under Python 3.11, all files <= 500 lines)
- **Summary:** Regenerated `frontend/src/lib/api-types.ts` via `scripts/gen-api-client.sh` to remove formatting and trailing whitespace drift introduced in #1512, ensuring `generate-api:check` passes cleanly in `Frontend Tests` CI on `main`.
- **Next step:** None (shipped in PR #1523).

### DL-#1493 · SC-B1-G10: Stream chat tokens live instead of after the process exits

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1493
- **Branch:** `feat/1493-live-token-streaming`
- **PR:** #1520
- **Paths:** `backend/staff/chat_streaming.py`, `backend/staff/chat.py`, `tests/unit/test_staff_chat_streaming.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (pytest tests/unit/test_staff_chat_streaming.py clean, all chat suites 57 passed, ruff clean, mypy clean, black clean, all files <= 500 lines)
- **Summary:** Incrementally read provider CLI stdout via background thread `LiveProcessReader` in `backend/staff/chat_streaming.py`, publishing token events on the ThreadEventBus as they arrive while the process is still running, recording accurate time-to-first-token (TTFT), terminating orphaned processes on cancellation, and maintaining identical reply contract semantics.
- **Next step:** None (shipped in PR #1520).

### DL-#1479 · K2: knowledge packs in the Staff Console

- **State:** shipped
- **Owner:** claude
- **Issue:** #1479
- **Branch:** `agy/issue-1479`
- **PR:** #1512
- **Paths:** `backend/knowledge_pack/`, `backend/staff/knowledge_refresh.py`, `backend/staff/chat.py`, `backend/staff/chat_knowledge.py`, `backend/routers/staff_v1.py`, `backend/routers/staff_knowledge.py`, `backend/staff/models.py`, `backend/staff/router_models.py`, `deploy/systemd-user/runner-dashboard-knowledge.service`, `deploy/systemd-user/runner-dashboard-knowledge.timer`, `frontend/src/lib/api-types.ts`, `frontend/src/lib/openapi.json`, `frontend/src/pages/StaffConsole/Roster.tsx`, `frontend/src/pages/StaffConsole/rosterUtils.ts`, `frontend/src/pages/StaffConsole/types.ts`, `tests/api/test_staff_knowledge_api.py`, `tests/knowledge/test_knowledge_pack_drift.py`, `tests/staff/routing_eval/dataset.py`, `tests/unit/test_knowledge_refresh.py`, `tests/unit/test_staff_chat_knowledge.py`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (review cleanup: reverted ruff-exclude weakening, rebuilt SPEC.md diff to a single change-log row, split knowledge injection out of `chat.py` into `chat_knowledge.py` and the two knowledge endpoints out of `staff_v1.py` into `staff_knowledge.py` to clear the 500-line cap, deduplicated pack-staleness logic into `knowledge_refresh.pack_is_stale`; `pytest tests/api/test_staff_knowledge_api.py tests/knowledge tests/unit/test_knowledge_refresh.py tests/unit/test_staff_chat_knowledge.py tests/unit/test_staff_chat*.py tests/staff/routing_eval` 63 passed/2 skipped; `mypy backend/` clean; `ruff check`/`ruff format --check` clean on backend/ and tests/; all touched backend files <= 500 lines)
- **Summary:** Vendored knowledge engine from Tools at pinned commit 09ff428af314969363f8908dcebafb84ddd7a3ef with drift test; added user systemd service and timer for pack refresh along with backend/staff/knowledge_refresh.py module; integrated retrieval-augmented chat turns for roles with search_knowledge tool injecting cited ## Knowledge block (now in `staff/chat_knowledge.py`); added GET /api/v1/staff/knowledge/{pack_id} and GET /api/v1/staff/knowledge/{pack_id}/search Pydantic endpoints (now in `routers/staff_knowledge.py`, mounted into `staff_v1.router`) and updated OpenAPI contract; added advisors roster group for disciple and vision-quest with routing keyword rules and eval dataset test cases.
- **Next step:** None (shipped in PR #1512).

### DL-#1513 · Restore green main: synchronize generated OpenAPI schema and TypeScript definitions for SC-B9 group threads

- **State:** shipped
- **Owner:** antigravity
- **Branch:** `fix/align-openapi-schema-python-311`
- **PR:** #1519
- **Paths:** `frontend/src/lib/openapi.json`, `frontend/src/lib/api-types.ts`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (generate-api:check clean via Python 3.11 uv sync, tsc clean, eslint clean, all files <= 500 lines)
- **Summary:** Aligned `frontend/src/lib/openapi.json` and `frontend/src/lib/api-types.ts` via `scripts/gen-api-client.sh` under Python 3.11 to capture `/api/v1/staff/groups/{group_id}/threads` and disambiguate `proposals__models__CreateProposalRequest`, resolving the failure in `Frontend Tests` on `main` push.
- **Next step:** None (shipped in PR #1519).

### DL-#1448 · SC-E3: Wire maintenance operations to real backends (slice 1: GitHub run cancel/rerun)

- **State:** shipped
- **Owner:** claude
- **Issue:** #1448
- **Branch:** `feat/1448-wire-maintenance`
- **PR:** #1483
- **Paths:** `backend/staff/maintenance_github.py`, `backend/staff/maintenance.py`, `backend/gh_client.py`, `backend/routers/staff_proposals.py`, `backend/routers/assistant.py`, `tests/staff/test_maintenance_github.py`, `tests/staff/test_maintenance_safety.py`, `tests/api/test_staff_maintenance_detect_api.py`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (tests/staff + tests/api + gh_client/slug tests: 1249 passed, 1 timing-flaky test_staff_runner case that passes 3/3 alone; mypy clean)
- **Summary:** Run cancel/rerun/cancel_and_rerun call GitHub via an anyio worker-thread bridge with classified faults and state-based verification; runner service, drain, group, purge, fleet_control, runner_remove and diagnose remain `not_wired` for later slices.
- **Next step:** None (shipped in PR #1483).

### DL-#1339 · SC-B9: Group threads: talk to the Board (and other groups) with the Board-Secretary coordinating seat replies

- **State:** in_review
- **Owner:** claude
- **Issue:** #1479
- **Branch:** `agy/issue-1479`
- **Paths:** `backend/knowledge_pack/`, `backend/staff/knowledge_refresh.py`, `backend/staff/chat.py`, `backend/staff/chat_knowledge.py`, `backend/routers/staff_v1.py`, `backend/routers/staff_knowledge.py`, `backend/staff/models.py`, `backend/staff/router_models.py`, `deploy/systemd-user/runner-dashboard-knowledge.service`, `deploy/systemd-user/runner-dashboard-knowledge.timer`, `frontend/src/lib/api-types.ts`, `frontend/src/lib/openapi.json`, `frontend/src/pages/StaffConsole/Roster.tsx`, `frontend/src/pages/StaffConsole/rosterUtils.ts`, `frontend/src/pages/StaffConsole/types.ts`, `tests/api/test_staff_knowledge_api.py`, `tests/knowledge/test_knowledge_pack_drift.py`, `tests/staff/routing_eval/dataset.py`, `tests/unit/test_knowledge_refresh.py`, `tests/unit/test_staff_chat_knowledge.py`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (review cleanup: reverted ruff-exclude weakening, rebuilt SPEC.md diff to a single change-log row, split knowledge injection out of `chat.py` into `chat_knowledge.py` and the two knowledge endpoints out of `staff_v1.py` into `staff_knowledge.py` to clear the 500-line cap, deduplicated pack-staleness logic into `knowledge_refresh.pack_is_stale`; `pytest tests/api/test_staff_knowledge_api.py tests/knowledge tests/unit/test_knowledge_refresh.py tests/unit/test_staff_chat_knowledge.py tests/unit/test_staff_chat*.py tests/staff/routing_eval` 63 passed/2 skipped; `mypy backend/` clean; `ruff check`/`ruff format --check` clean on backend/ and tests/; all touched backend files <= 500 lines)
- **Summary:** Vendored knowledge engine from Tools at pinned commit 09ff428af314969363f8908dcebafb84ddd7a3ef with drift test; added user systemd service and timer for pack refresh along with backend/staff/knowledge_refresh.py module; integrated retrieval-augmented chat turns for roles with search_knowledge tool injecting cited ## Knowledge block (now in `staff/chat_knowledge.py`); added GET /api/v1/staff/knowledge/{pack_id} and GET /api/v1/staff/knowledge/{pack_id}/search Pydantic endpoints (now in `routers/staff_knowledge.py`, mounted into `staff_v1.router`) and updated OpenAPI contract; added advisors roster group for disciple and vision-quest with routing keyword rules and eval dataset test cases.
- **Next step:** Push branch; PR stays draft pending owner review.

### DL-#1339 · SC-B9: Group threads: talk to the Board (and other groups) with the Board-Secretary coordinating seat replies

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1339 (epic #1348 / umbrella #1354)
- **Branch:** `feat/1339-group-threads-board`
- **PR:** #1480
- **Paths:** `backend/routers/staff_groups.py`, `backend/staff/group_models.py`, `backend/staff/groups.py`, `backend/routers/staff_threads.py`, `backend/staff/action_executors.py`, `backend/staff/actions.py`, `backend/server.py`, `tests/unit/test_staff_groups.py`, `tests/api/test_staff_groups_api.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (pytest tests/unit/test_staff_groups.py tests/api/test_staff_groups_api.py 15/15 passing; regression test suite 42/42 passing; ruff check clean; ruff format clean; mypy 0 issues; all files strictly <= 500 lines)
- **Summary:** Enabled group threads coordinating seat replies: (1) Added data models (`group_models.py`) and group coordination engine (`groups.py`) configuring Board group (seats Alpha, Bravo, Charlie, Delta + coordinator `board-secretary`); (2) Implemented concurrent fanout across seat chat turns with per-seat timeout and fault isolation; (3) Added consensus synthesis with quorum threshold (3/4), executive synthesis, collapsible `<details><summary>` seat disclosures, and automatic `board.propose` ActionProposal creation; (4) Added pre-send token/USD cost estimation and threshold guard (`STAFF_GROUP_COST_THRESHOLD_USD`, default $2.00) requiring `confirm_cost=True`; (5) Added REST endpoints `GET /api/v1/staff/groups`, `GET /api/v1/staff/groups/{id}`, `GET /api/v1/staff/groups/{id}/cost-estimate`, `POST /api/v1/staff/groups/{id}/threads`; (6) Integrated group threads in `staff_threads.py` with async background coordinator runner; (7) Authorized `board-secretary` for `board.propose` and `staff.dispatch` actions; (8) Fixed `create_work_item` parameter naming in `action_executors.py`.
- **Next step:** None (shipped in PR #1480).

### DL-#1484 · SC-B1-G1: Enforce read-only chat turns per provider

- **State:** shipped
- **Owner:** claude
- **Issue:** #1484
- **PR:** #1506
- **Branch:** `fix/1484-read-only-chat`
- **Paths:** `backend/staff/adapters.py`, `backend/staff/chat.py`, `backend/staff/chat_failures.py`, `backend/staff/validator.py`, `tests/unit/test_staff_chat_read_only.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (new suite 24 passed; staff/chat/role/adapter/validator selection 618 passed, 15 skipped; ruff clean; `mypy backend/` clean in 249 files; chat.py 492 lines)
- **Summary:** Every chat argv carries the provider's explicit read-only flag on fresh and resumed turns (the resumed claude turn had none); claude additionally denies the write tools; providers without a read-only mode fail closed with `provider_not_read_only`; `chat.read_only_tools` is a validated provider-neutral vocabulary mapped to the claude `--allowedTools` allowlist. `cursor-agent --mode ask` could not be verified locally (CLI not installed on DeskComputer); an unknown flag fails the turn visibly, never writable.
- **Next step:** None (shipped in PR #1506). Verify a live cursor-agent chat turn on a node that has the CLI.

### DL-#1494 · Fix projects run steward missing Idempotency-Key

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1494
- **PR:** #1509
- **Branch:** `fix/1494-projects-run-steward-idempotency`
- **Paths:** `frontend/src/pages/ProjectsPage.tsx`, `frontend/src/pages/__tests__/Projects.test.tsx`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (Projects.test.tsx 8/8 passed, tsc 0 errors, eslint 0 errors, all files <= 500 lines, CI 100% green on PR #1509 and main)
- **Summary:** Replaced raw `apiRequest` in `ProjectsPage.tsx` with shared `dispatchRun("project-steward", ...)` and `errorMessage` from `frontend/src/pages/Staff/staffApi.ts`. `dispatchRun` automatically generates and sets the required `Idempotency-Key` header and CSRF sentinel header, satisfying `require_idempotency_header` on `/api/v1/staff/project-steward/run`. Formatted error messages via `errorMessage` to present user-friendly error details. Added Vitest assertions in `Projects.test.tsx` verifying `Idempotency-Key` presence and surfacing of 400 Bad Request error details.
- **Next step:** None (shipped in PR #1509).

### DL-#1492 · SC-B1-G9: Chat pool saturation rejects turns with chat_capacity

- **State:** in_review
- **Owner:** antigravity
- **Issue:** #1492
- **Branch:** `fix/1492-chat-pool-saturation-busy`
- **Paths:** `backend/staff/chat_pool.py`, `backend/staff/chat_failures.py`, `backend/staff/chat.py`, `tests/unit/test_staff_chat_capacity.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (tests/unit/test_staff_chat_capacity.py passed, pytest 25 passed across chat suites, ruff clean, mypy clean, all files <= 500 lines)
- **Summary:** Implemented `ChatConcurrencyPool.acquire(role, timeout)` respecting total slot capacity and Barb reservation, configured `DEFAULT_CHAT_ACQUIRE_TIMEOUT = 5.0` seconds, added `record_chat_capacity_failure()` in `backend/staff/chat_failures.py` updating placeholder messages to failed with failure class `chat_capacity`, and updated `ChatTurnRunner.execute_turn` to acquire a slot before dispatching, immediately returning retryable `chat_capacity` failure on saturation without attempting LLM execution or skewing successful turn metrics. Added dedicated unit test suite in `tests/unit/test_staff_chat_capacity.py`.
- **Next step:** Push branch, open PR, enable auto-merge, verify CI passes.

### DL-#1483 · Restore green main: resolve a11y violations in staff RosterRow, ContextPane, and theme danger badges

- **State:** shipped
- **Owner:** antigravity
- **Branch:** `fix/restore-green-main-danger-badge-contrast`
- **PR:** #1507
- **Paths:** `frontend/src/pages/StaffConsole/ContextPane.tsx`, `frontend/src/pages/StaffConsole/RosterRow.tsx`, `frontend/src/design/fleetThemes.ts`, `frontend/src/design/tokens.ts`, `frontend/src/design/__tests__/fleetThemes.contrast.test.ts`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (StaffConsole vitest 17/17 passed; fleetThemes vitest 24/24 passed; npm run typecheck clean; npm run lint clean; all files <= 500 lines)
- **Summary:** Wrapped role avatar and details in an accessible button and removed `role="button"` and `tabIndex={0}` from outer roster row container (resolving WCAG 4.1.2 nested-interactive). Replaced unconfigured `--color-*` variables in `ContextPane.tsx` with standard design system tokens. Adjusted `light.semantic.error` in `fleetThemes.ts` and `lightBadgeTokens` in `tokens.ts` from `#bf2130` to `#b81d2c`, raising contrast on tinted backgrounds (`--badge-danger-bg` over `var(--bg-secondary)`) from 4.49:1 to 4.84:1 to strictly satisfy WCAG AA 4.5:1 minimums, resolving axe-core `color-contrast` failures in Playwright E2E smoke tests.
- **Next step:** None (shipped in PR #1507).

### DL-#1475 · WP-0.2: Show Board proposals in the owner inbox (wire inbox to the CR-7 store)

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1475
- **Branch:** `fix/wp-0.2-inbox-board-proposals-1475`
- **PR:** #1482
- **Paths:** `backend/staff/inbox.py`, `backend/staff/briefings.py`, `tests/unit/test_staff_inbox_proposals.py`, `frontend/src/pages/Staff/InboxPanel.tsx`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (pytest tests/unit/ 100% passed; vitest 1337/1337 passed; npm run typecheck 0 errors; ruff check & format clean; mypy clean in 248 source files; all files <= 500 lines)
- **Summary:** Replaced the stub in `backend/staff/inbox.py` with `_collect_board_proposals()` reading open proposals from CR-7 store `proposals.store.list_github_proposals(state="open")` with `DEFAULT_CACHE_TTL` caching. Excluded decided and closed proposals, mapped open proposals waiting on decisions to `InboxItem` with `source="board_proposal"`, severity mapped from urgency, age from `created_at`, link `/staff/fleet-command?section=proposals`, and structured metadata. Extracted briefing generation to `backend/staff/briefings.py` to keep all files strictly <= 500 lines. Added Proposals filter pill to frontend `InboxPanel.tsx`. Added unit test suite in `tests/unit/test_staff_inbox_proposals.py`.
- **Next step:** None (shipped in PR #1482).

### DL-#1338 · SC-G6: Retire the Cline Launcher page and its agent-launcher API

- **State:** shipped
- **Owner:** claude
- **Issue:** #1338 (Cline Launcher slice only; the other pages in #1338 remain `judgement:contested` pending owner decisions)
- **Branch:** `chore/1338-retire-cline-launcher`
- **PR:** #1467
- **Paths:** `frontend/src/shell/routing.ts`, `frontend/src/shell/navRegistryData.ts`, `frontend/src/shell/RoutedShell.tsx`, `frontend/src/shell/intro.ts`, `frontend/src/legacy/App.tsx`, `backend/server.py`, `frontend/src/lib/openapi.json`, `frontend/src/lib/api-types.ts`, `tests/test_retired_cline_launcher.py`, `frontend/src/shell/__tests__/retiredClineLauncher.test.ts`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (vitest 1309 passed; tsc clean; targeted pytest 81 passed, 1 skipped)
- **Summary:** Owner decided to retire the Cline Launcher. Page, nav entry, intro override, legacy tab and `/api/agent-launcher` router removed; old addresses redirect to the Staff Console.
- **Next step:** None (shipped in PR #1467).

### DL-#1446 · Staff Console end to end: desktop three-pane console and real thread resolution

- **State:** shipped
- **Owner:** claude
- **Issue:** #1446 (epic #1350 / umbrella #1354)
- **Branch:** `feat/1446-desktop-staff-console`
- **PR:** #1467
- **Paths:** `frontend/src/pages/StaffConsole/useStaffConsole.ts`, `frontend/src/pages/StaffConsole/consoleThreads.ts`, `frontend/src/pages/StaffConsole/Desktop.tsx`, `frontend/src/pages/StaffConsole/desktop.css`, `frontend/src/pages/StaffConsole/ConsoleErrorBanner.tsx`, `frontend/src/pages/StaffConsole/Mobile.tsx`, `frontend/src/pages/StaffConsole/index.ts`, `frontend/src/pages/Staff/StaffPage.tsx`, `frontend/src/pages/Staff/staffApi.ts`, `tests/e2e/a11y.spec.ts`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (vitest frontend/src/pages 86 files/673 tests + StaffPageConsole; tsc -p tsconfig.app.json 0 errors; changed files within the 500-line cap)
- **Summary:** Desktop console is the default Staff section; desktop and mobile share `useStaffConsole`; roles open server-resolved threads (no invented ids); every backend failure is a visible alert.
- **Next step:** None (shipped in PR #1467).

### DL-#1344 · SC-E7: Maintenance safety tests: approval gates, blast-radius limits and fault injection

- **State:** shipped
- **Owner:** claude
- **Issue:** #1344 (epic #1351 / umbrella #1354); follow-up #1448 wires the stub operations
- **Branch:** `test/1344-maintenance-safety`
- **PR:** #1467
- **Paths:** `backend/staff/maintenance.py`, `backend/staff/maintenance_policy.py`, `backend/staff/maintenance_detect.py`, `tests/staff/test_maintenance_safety.py`, `tests/unit/test_staff_maintenance_detect.py`, `tests/api/test_staff_maintenance_detect_api.py`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (maintenance/actions/proposals/safety pytest 197 passed, 12 skipped; ruff clean; mypy backend/ clean)
- **Summary:** Pinned policy table with a mutation check; fleet-wide, single-target and batch-size gates read from it; detector risk comes from the registry; unwired operations fail as `not_wired`; timeouts, token expiry and partial failures are classified and audited.
- **Next step:** None (shipped in PR #1467).

### DL-#1474 · WP-0.1: Resolve staff action role names against the loaded roster

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1474
- **Branch:** `fix/wp-0.1-resolve-staff-action-roles-1474`
- **PR:** #1481
- **Paths:** `backend/staff/action_executors.py`, `tests/staff/routing_eval/test_action_executor_roles.py`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (pytest 5/5 passed in test_action_executor_roles.py, 19/19 passed in tests/staff/ and test_staff_actions.py; ruff check and format clean; mypy backend clean with 0 issues in 247 files; all files <= 500 lines)
- **Summary:** Replaced literal unresolvable staff role strings in `backend/staff/action_executors.py` with module constants: `DEFAULT_REVIEWER_ROLE = "fleet-critic"`, `CODE_REQUEST_OWNER_ROLE = "barb"`, `BOARD_PROPOSAL_ROLE = "board-secretary"`. Added `validate_action_default_roles` to validate default roles against `load_roles()`, logging warnings without crashing at runtime and failing loudly on error in tests. Added unit test suite in `tests/staff/routing_eval/test_action_executor_roles.py`.
- **Next step:** None (shipped in PR #1481).

### DL-#1477 · Staff validator accepts RM tool/scope grants

- **State:** shipped
- **Owner:** claude
- **Issue:** #1477
- **Branch:** `fix/staff-validator-tools-scopes`
- **PR:** #1478
- **Paths:** `backend/staff/validator.py`, `backend/staff/schema.json`, `tests/unit/test_staff_roles.py`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (pytest -k 'staff or role': 477 passed; ruff + mypy clean; live RM roster loads with 0 invalid roles)
- **Summary:** RD's hand-written staff validator rejected the RM `tools`/`scopes` fields, marking three roles invalid and undispatchable; both are now optional unique string lists, RM stays the vocabulary authority.
- **Next step:** None (shipped in PR #1478).

### DL-#1287 · CR-5: Executor stage — route planned issues to cheaper agents with claims, escalation and rollup

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1287
- **Branch:** `feat/cr-5-executor-stage-1287`
- **PR:** #1476
- **Paths:** `backend/code_requests/executor_models.py`, `backend/code_requests/executor_router.py`, `backend/code_requests/executor_coordination.py`, `backend/code_requests/executor_stage.py`, `backend/routers/code_requests_executor.py`, `backend/server.py`, `tests/code_requests/test_executor_stage.py`, `tests/code_requests/test_executor_routes.py`, `frontend/src/lib/api-types.ts`, `frontend/src/lib/openapi.json`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (pytest 83/83 passed in tests/code_requests/ across test_executor_stage and test_executor_routes; vitest 1313/1313 passed; npm run generate-api:check exit 0; npm run typecheck 0 errors; npm run lint 0 warnings; ruff check clean; all files <= 500 lines)
- **Summary:** Implemented CR-5 Executor stage: (1) Execution models and tier classification (`ollama`, `cli`, `strong`) with task class mappings and profile routing; (2) Multi-agent coordination with roster priority (`user > maxwell-daemon > claude > codex > conductor > jules > local > gaai`), claim checking, lease acquisition with 2h TTL, and PR metadata generation; (3) Wave-based dependency scheduler with topological acyclic ordering and per-repo concurrency caps; (4) Retries, failure tier escalation after 2 failures, human triage pausing (`needs-human-triage`) upon strong exhaustion, downstream dependency blocking; (5) Endpoints for initialization, dispatch, child reporting, and rollup mounted in `backend/routers/code_requests_executor.py`.
- **Next step:** None (shipped in PR #1476).

### DL-#1471 · CI: Synchronize generated OpenAPI contract types for Staff and Board proposal requests

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1471
- **Branch:** `fix/sync-openapi-proposal-schemas-1471`
- **PR:** #1473
- **Paths:** `scripts/gen-api-client.sh`, `frontend/src/lib/api-types.ts`, `frontend/src/lib/openapi.json`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (`npm run generate-api:check` exit 0, `npm run typecheck` 0 errors, `npm run lint` 0 warnings, vitest 1313/1313 passed, pytest 61/61 code requests passed, ruff clean)
- **Summary:** Synchronized generated OpenAPI schema and TypeScript definitions for Staff proposals and Board proposals (`CreateProposalRequest` schema naming mapping in `frontend/src/lib/openapi.json` and `frontend/src/lib/api-types.ts`), ensuring `scripts/gen-api-client.sh --check` passes cleanly in CI.
- **Next step:** None (shipped in PR #1473).

### DL-#1286 · CR-6: Board routing gate for new/significant Code Requests

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1286
- **Branch:** `feat/issue-1286-board-routing-gate`
- **PR:** #1469
- **Paths:** `backend/code_requests/board_gate.py`, `backend/routers/code_requests_board.py`, `backend/server.py`, `tests/code_requests/test_board_gate.py`, `tests/code_requests/test_board_gate_routes.py`, `frontend/src/lib/api-types.ts`, `frontend/src/lib/openapi.json`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (pytest 33/33 passed across test_board_gate and test_board_gate_routes; 61/61 passed across tests/code_requests/; vitest 1309/1309 passed; ruff check and format clean; mypy clean; line cap check <= 500 lines passed)
- **Summary:** Implemented Board routing gate for Code Requests evaluating 7 architectural criteria, confidential InEnTec data egress user sign-off check, operator overrides (`force_board` / `skip_board`) requiring `operator` role and reason, automatic proposal creation via CR-7 proposal API, decision syncing (`board:accepted`, `board:declined`, `board:deferred`), and escalation deadline checks. Mounted endpoints in `backend/routers/code_requests_board.py` and `backend/server.py`.
- **Next step:** None (shipped in PR #1469).

### DL-#1330 · SC-D11: Fold the three stray chat surfaces (Maxwell chat, Codebase chat, legacy assistant sidebar) into the Staff Console

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1330 (epic #1352 / umbrella #1354)
- **Branch:** `feat/1330-unify-chat-surfaces`
- **PR:** #1435
- **Paths:** `backend/routers/assistant.py`, `backend/staff/adapters.py`, `backend/staff/router_models.py`, `frontend/src/pages/AssistantSidebar.tsx`, `frontend/src/pages/Maxwell/CodebaseChat.tsx`, `frontend/src/pages/MaxwellPanels.tsx`, `frontend/src/pages/__tests__/AssistantSidebar.test.tsx`, `frontend/src/shell/HelpAbout.tsx`, `frontend/src/shell/__tests__/HelpAbout.test.tsx`, `tests/test_assistant_retirement.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (Merged to main via PR #1435, all 22 CI checks passed)
- **Summary:** Folded stray chat surfaces into the Staff Console: (1) Updated `POST /api/assistant/chat` to return HTTP 410 Gone with successor Link header pointing to `/api/v1/staff/threads` and Sunset header; (2) Folded codebase Q&A into Cartographer and Librarian with role handoff cards and `onNavigate` in `HelpAbout.tsx` and `CodebaseChat.tsx`; (3) Added codebase Q&A routing keywords to `cartographer` and `librarian` and registered `maxwell` in `ROLE_KEYWORD_RULES` and provider `ADAPTERS`; (4) Added Staff Console integration link and multi-agent context to Maxwell Chat panel (`MaxwellPanels.tsx`); (5) Added retirement notice banner and 410 redirect handling in `AssistantSidebar.tsx`.
- **Next step:** None (shipped in PR #1435).

### DL-#1345 · SC-G7: Retire the Classic layout and legacy/App.tsx

- **State:** in_review
- **Owner:** claude
- **Issue:** #1345 (epic #1353)
- **Branch:** `feat/1345-remove-legacy-layout` (first step, mobile Projects, was `fix/1345-mobile-projects`)
- **PR:** not created
- **Paths:** `frontend/src/shell/RoutedShell.tsx`, `frontend/src/shell/layoutFlag.ts`, `frontend/src/shell/shellActions.ts`, `frontend/src/shell/SessionExpiredDialog.tsx`, `frontend/src/shell/index.ts`, `frontend/src/main.tsx`, `frontend/src/lib/fetchGuards.ts`, `frontend/src/lib/sessionExpired.ts`, `frontend/src/lib/wheelValueGuard.ts`, `frontend/src/lib/api.ts`, `frontend/src/shell/__tests__/`, `frontend/src/lib/__tests__/`, `tests/test_frontend_integrity.py`, `tests/test_today_ui_redesign.py`, `tests/test_no_duplicate_top_level_functions.py`, `tests/frontend/test_color_literal_budget.py`, `.eslintrc.json`, `vitest.config.ts`, `CLAUDE.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 at 6f1fe99f plus this branch (vitest 159 files / 1355 passed; frontend static pytest 222 passed; `tsc` and eslint clean; `npm run build` total JS 1,112,452 → 1,007,689 bytes)
- **Summary:** The mobile drawer's Projects entry fell back to the legacy App, which had no projects case (fixed first). Now `legacy/App.tsx`, `RecoveryDialog` and `visibleInterval` are deleted and nothing imports `legacy/`. Mobile tabs without a mobile page render the desktop page; the Classic layout action is gone and a stored `dashboard.layout` is removed with one notice. `sessionExpired`/`fetchGuards`/`wheelValueGuard` moved to `lib/` and the Session Expired dialog to `shell/`; `main.tsx` installs the guards and `RoutedShell` mounts `SessionExpiredHost`, because before this only the Classic layout did, so an expired session failed silently in the modern shell.
- **Next step:** Merge, then run SC-G8 (#1346) to delete the modules this leaves unmounted (`AssistantSidebar`, `DashboardHelp`, `AlertsCenter`, `QuickDispatch`).

### DL-#1340 · SC-C7: Routing evaluation set and regression check for Barb

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1340 (epic #1349 / umbrella #1354)
- **Branch:** `feat/1340-barb-routing-eval`
- **Paths:** `tests/staff/routing_eval/models.py`, `tests/staff/routing_eval/dataset.py`, `tests/staff/routing_eval/engine.py`, `tests/staff/routing_eval/test_barb_routing_eval.py`, `scripts/eval_barb_routing.py`, `backend/routers/staff_routing.py`, `tests/api/test_staff_routing_api.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (Merged to main via PR #1442, all 22 CI checks passed)
- **Summary:** Implemented SC-C7 routing evaluation set and regression check for Barb: (1) Curated 80-case evaluation dataset across 10 categories with expected targets and clarify/answer outcomes; (2) Created CI regression test suite running deterministic pre-router; (3) Built `scripts/eval_barb_routing.py` CLI runner for evaluating full router accuracy, supporting `--post-board` proposal creation; (4) Added candidate feedback ingestion from routing overrides (`load_candidate_cases_from_feedback()`); (5) Exposed `GET /api/v1/staff/routing/eval` REST endpoint.
- **Next step:** Merged PR #1442 to main via auto-merge.

### DL-#1459 · Restore green main: split frontend FleetCommand test suite strictly <= 500 lines

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1459
- **Branch:** `fix/issue-1459-split-fleetcommand-tests`
- **Paths:** `frontend/src/pages/__tests__/FleetCommand.test.tsx`, `frontend/src/pages/__tests__/FleetCommandOps.test.tsx`, `frontend/src/pages/__tests__/fleetCommandTestHelpers.ts`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (Merged to main via PR #1460)
- **Summary:** Extracted shared test fixtures/helpers into `fleetCommandTestHelpers.ts` (168 lines), kept core coordination panels in `FleetCommand.test.tsx` (232 lines), and operations tests in `FleetCommandOps.test.tsx` (198 lines), strictly satisfying the <= 500 line limit to restore green main.
- **Next step:** None (shipped in PR #1460).

### DL-#1284 · CR-7: Board Proposals suggestion box — API, Fleet Command tab, fleet tool

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1284
- **Branch:** `agy/issue-1284`
- **Paths:** `backend/identity.py`, `backend/gh_utils.py`, `backend/proposals/__init__.py`, `backend/proposals/models.py`, `backend/proposals/service.py`, `backend/proposals/store.py`, `backend/routers/proposals.py`, `backend/server.py`, `backend/middleware.py`, `clients/fleet/fleet_client.py`, `clients/fleet/fleet_tools.py`, `clients/fleet/fleet_validators.py`, `frontend/src/pages/FleetCommand/ProposalsPanel.tsx`, `frontend/src/pages/FleetCommand/ProposalForm.tsx`, `frontend/src/pages/FleetCommand/ProposalLists.tsx`, `frontend/src/pages/FleetCommand/FleetCommandPage.tsx`, `frontend/src/pages/FleetCommand/PrioritiesPanel.tsx`, `frontend/src/pages/FleetCommand/fleetApi.ts`, `frontend/src/pages/FleetCommand/index.ts`, `frontend/src/pages/FleetCommand/types.ts`, `frontend/src/pages/CodeRequestsHistory.tsx`, `frontend/src/lib/api-types.ts`, `frontend/src/lib/openapi.json`, `tests/unit/test_proposals_store.py`, `tests/api/test_proposals_routes.py`, `tests/clients/test_fleet_client_proposals.py`, `tests/clients/test_fleet_cli.py`, `tests/clients/test_fleet_mcp.py`, `frontend/src/pages/__tests__/ProposalsPanel.test.tsx`, `frontend/src/pages/__tests__/FleetCommand.test.tsx`, `frontend/src/pages/__tests__/CodeRequests.test.tsx`, `SPEC.md`, `docs/development/HANDOFF.md`, `docs/development/DEVELOPMENT_LOG.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (Merged to main via PR #1444, all 21 CI checks passed)
- **Summary:** Implemented suggestion box for humans and agents submitting proposals to the Board stored as GitHub issues in `D-sorganization/Repository_Management` with label `board:proposal` and `needs-decision`.

### DL-#1346 · SC-G8: Delete dead frontend code

- **State:** shipped
- **Owner:** claude
- **Issue:** #1346 (epic #1353)
- **Branch:** `chore/1346-dead-frontend`
- **PR:** #1451
- **Paths:** `frontend/src/primitives/`, `frontend/src/lib/schemas/dispatch.ts`, `package.json`, `package-lock.json`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 at `a34c322b` baseline (vitest 1299 passed; tsc clean; bundle 1,169,405 B before and after)
- **Summary:** Removes the never-mounted primitives and the dependencies only they used. QuickDispatch and AlertsCenter remain until the legacy App is removed.
- **Next step:** Merged PR #1451, delete QuickDispatch and AlertsCenter together with `legacy/App.tsx` under #1345.

### DL-#1282 · CR-2: Code Request data model, lifecycle state machine and durable GitHub-backed record

- **State:** shipped
- **Owner:** local
- **Issue:** #1282 (epic #1279)
- **Branch:** `feat/1282-code-request-model-store`
- **Paths:** `backend/code_requests/model.py`, `backend/code_requests/lifecycle.py`, `backend/code_requests/store.py`, `backend/code_requests/dispatch.py`, `backend/code_requests/schema.md`, `backend/routers/code_requests.py`, `backend/dispatch/audit.py`, `backend/gh_utils.py`, `docs/code-requests.md`, `scripts/ensure_code_request_labels.py`, `tests/code_requests/test_lifecycle.py`, `tests/code_requests/test_store.py`, `tests/api/test_code_requests.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (pytest all 18 passing; mypy 0 errors in 8 files; ruff check clean; ruff format clean; npm run typecheck clean; npm run lint clean; all files strictly <= 500 lines)
- **Summary:** Implemented CR-2: (1) Pydantic models for CodeRequest, CodeRequestState, BoardRoute, Requester, CodeRequestAuditEvent with lossless YAML front-matter serialization & parsing; (2) Pure-function lifecycle state machine with legal transitions and operator overrides; (3) GitHub issue-backed durable CodeRequestStore with local JSON cache fallback and automatic cache rebuilds; (4) Dispatch helpers with standards prompt injection (TDD, DbC, DRY, LoD, security, docs); (5) REST API endpoints GET/POST /api/code-requests, GET /api/code-requests/{id}, and POST /api/code-requests/{id}/transition with dual-scope authorization (code-requests.manage and feature-requests.manage); (6) ensure_code_request_labels.py script; (7) Complete documentation and test suites.
- **Next step:** Merged PR #1443 to main via auto-merge.

### DL-#1434 · Projects: fleet-wide prioritised status, charter coverage and untracked-work report

- **State:** shipped
- **Owner:** claude
- **Issue:** #1434 (epic #1192)
- **Branch:** `feat/fleet-project-tracking`
- **PR:** #1441
- **Paths:** `backend/projects/priorities.py`, `backend/projects/coverage.py`, `backend/projects/rollup.py`, `backend/projects/service.py`, `backend/routers/projects.py`, `config/projects.json`, `frontend/src/pages/Projects/`, `frontend/src/pages/ProjectsPage.tsx`, `frontend/src/lib/openapi.json`, `frontend/src/lib/api-types.ts`, `tests/api/test_projects_tracking.py`, `tests/api/test_projects_router.py`, `frontend/src/pages/__tests__/Projects.test.tsx`, `docs/projects.md`, `SPEC.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 at 8baa2bf (pytest projects suites 36 passed; vitest Projects 7 passed; tsc clean; ruff/mypy clean; gen-api-client regenerated)
- **Summary:** Owner priority tiers from Repository_Management `config/project_priorities.yaml`, P0-first ordering and a fleet summary on `GET /api/projects`, per-repo coverage of open issues/PRs by charter features, and `GET /api/projects/untracked` as the fleet-curator worklist; Projects tab shows tier, coverage and the summary.
- **Next step:** None: merged as `b2a8aaed`; the owner tiers land with Repository_Management#1761.

### DL-#1343 · SC-D9: Accessibility and keyboard pass on the Staff Console

- **State:** shipped
- **Owner:** antigravity (implementation), claude (review and rework)
- **Issue:** #1343 (epic #1350 / umbrella #1354)
- **Branch:** `agy/issue-1343`
- **PR:** #1433
- **Paths:** `frontend/src/pages/StaffConsole/`, `frontend/src/shell/HelpAbout.tsx`, `tests/e2e/a11y.spec.ts`, `tests/e2e/mobile.spec.ts`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (`f9333a9c` baseline; vitest 93/93, tsc 0 errors)
- **Summary:** The thread is a polite `role="log"` and the only live region. Focus follows a thread switch through the Composer (`focusOnThreadChange`); on mobile it goes to the heading and back to search. Focus rings are visible, scrolling respects reduced motion, status labels are clearer, and the Staff shortcuts are listed in the global `?` Help panel. The axe and keyboard-walkthrough e2e tests assert for real.
- **Next step:** Shipped in PR #1433 (commit `5c21226`).

### DL-#1281 · CR-1: Rename Feature Requests → Code Requests with back-compat aliases

- **State:** shipped
- **Owner:** local
- **Issue:** #1281 (epic #1279)
- **Branch:** `feat/1281-code-requests-rename`
- **Paths:** `backend/identity.py`, `backend/routers/code_requests.py`, `backend/routers/feature_requests.py`, `backend/routers/usage_metrics.py`, `backend/server.py`, `frontend/src/pages/codeRequestsTypes.ts`, `frontend/src/pages/CodeRequests.tsx`, `frontend/src/pages/CodeRequestsHistory.tsx`, `frontend/src/pages/CodeRequestsPage.tsx`, `frontend/src/pages/FeatureRequests.tsx`, `frontend/src/pages/FeatureRequestsPage.tsx`, `frontend/src/legacy/App.tsx`, `frontend/src/shell/navRegistryData.ts`, `frontend/src/shell/routing.ts`, `frontend/src/shell/RoutedShell.tsx`, `frontend/src/lib/openapi.json`, `frontend/src/lib/api-types.ts`, `tests/api/test_code_requests.py`, `frontend/src/pages/__tests__/CodeRequests.test.tsx`, `frontend/src/pages/__tests__/CodeRequestsPage.test.tsx`, `tests/test_frontend_integrity.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (pytest all passing; 153/153 frontend vitest test suites passing with 1302 tests; npm run typecheck clean; ruff check clean; ruff format clean; mypy clean; bash scripts/gen-api-client.sh --check clean; all files strictly <= 500 lines)
- **Summary:** Renamed Feature Requests to Code Requests throughout backend and frontend while maintaining complete backward compatibility: (1) Added `/api/code-requests`, `/api/code-requests/templates`, `/api/code-requests/dispatch` routes and preserved `/api/feature-requests*` as thin deprecated aliases returning `Deprecation: true` and `Link: </api/code-requests...>; rel="successor-version"`; (2) Added `code-requests.manage` scope aliased bidirectionally with `feature-requests.manage` in `backend/identity.py`; (3) Idempotently migrated stored history from `feature_requests.json` to `code_requests.json` with `.migrated` marker without deleting original; (4) Added `frontend/src/pages/CodeRequests.tsx`, `CodeRequestsHistory.tsx`, `CodeRequestsPage.tsx`, `codeRequestsTypes.ts` with shims in `FeatureRequests*.tsx`, updated nav tab to `code-requests` ("Code Requests") with redirect from `feature-requests`; (5) Synchronized OpenAPI schema and generated TypeScript client types; (6) Updated docs and test integrity suites.
- **Next step:** Shipped in PR #1432.

### DL-#1327 · SC-C4: Barb follow-up engine: detect stalled, failed, blocked and waiting work; retry, re-route or escalate

### DL-#1333 · SC-E6: Maintenance in the UI: Maintenance thread plus "Ask Maintenance" row actions on the Fleet page

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1333 (epic #1351 / umbrella #1354)
- **Branch:** `feat/1333-maintenance-ui`
- **Paths:** `frontend/src/pages/Fleet/fleetActions.ts`, `frontend/src/pages/Fleet/FleetRowActions.tsx`, `frontend/src/pages/Fleet/MaintenanceActionModal.tsx`, `frontend/src/pages/Fleet/FleetMachinesSection.tsx`, `frontend/src/pages/Fleet/FleetRunnersSection.tsx`, `frontend/src/pages/Fleet/index.ts`, `frontend/src/pages/OverviewPage.tsx`, `frontend/src/pages/StaffConsole/cards/ActionCard.tsx`, `frontend/src/pages/StaffConsole/cards/cardTypes.ts`, `backend/staff/router_models.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (vitest 72/72 tests passing across Fleet and cards; npm run typecheck 0 errors; npm run lint 0 warnings; ruff check clean; pytest router & maintenance 12/12 passing; test_color_literal_budget passing; all files <= 500 lines)
- **Summary:** Implemented SC-E6 Maintenance row actions on the Fleet page: (1) Added accessible `FleetRowActions` dropdown menu for machines and runners covering 5 actions ("Bring online", "Take offline", "Restart", "Compact disk", "Diagnose"); (2) Followed Owner decision (2026-09-23) routing mutating actions through Barb to Maintenance, and read-only actions (Diagnose) directly to Maintenance; (3) Added dry-run display with planned steps, verification confirmations, and Barb routing badges to `ActionCard`; (4) Built `MaintenanceActionModal` presenting pre-filled action card with dry-run shown, executing upon approval, verifying postcondition state cleanly, and refreshing fleet data; (5) Added maintenance action keyword rules to `router_models.py`.
- **Next step:** Shipped in PR #1430.

### DL-#1325 · SC-G3: Fleet -> Operations: merge Deployment, Fleet Orchestration, Diagnostics, Conductor, Runner Plan and Schedules

- **State:** shipped
- **Owner:** local
- **Issue:** #1327 (epic #1350 / umbrella #1354)
- **Branch:** `feat/1327-barb-followup`
- **Paths:** `backend/staff/followup.py`, `backend/routers/staff_followup.py`, `backend/server.py`, `backend/staff/store.py`, `backend/fleet_events.py`, `tests/api/test_staff_followup.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (pytest tests/api/test_staff_followup.py 9/9 passed; full staff suite 261 passed; ruff, black, mypy all passed 0 errors; all files strictly <= 500 lines)
- **Summary:** Implemented SC-C4 Barb follow-up engine: (1) `FollowupEngine` coordinates periodic idempotent sweep (default 300s) detecting overdue work items, stalled or failed runs, waiting-on-user work, and invalid owners; (2) condition playbooks: retryable/stalled runs retried once up to max_attempts; second failure escalates to Barb's conversation thread and fires critical Web Push `staff.escalation`; auth expired runs post action item notice to Barb's thread; needs-input prompts the owner in Barb's thread; wrong owners re-routed to default role with SC-A8 audit log; (3) debounce & idempotency guarantees (at most 1 follow-up per item per interval); (4) watchdog detects missed sweeps ($\ge 2$ intervals), emitting critical fleet event `barb_followup_watchdog` and `staff.escalation` Web Push; (5) daily digest counts (`closed`, `retried`, `rerouted`, `escalated`, `still_open`); (6) REST API endpoints `POST /api/v1/staff/followup/sweep`, `GET /api/v1/staff/followup/status`, `GET /api/v1/staff/followup/digest`.
- **Next step:** Push branch `feat/1327-barb-followup`, open PR with Fixes #1327, enable auto-merge, monitor CI to green merge, release lease, and clean up.

### DL-#1325 · SC-G3: Fleet -> Operations: merge Deployment, Fleet Orchestration, Diagnostics, Conductor, Runner Plan and Schedules

- **State:** shipped
- **Owner:** local
- **Issue:** #1325 (epic #1353 / umbrella #1354)
- **Branch:** `feat/1325-operations-merge`
- **Paths:** `frontend/src/pages/Operations/OperationsStatusBanner.tsx`, `frontend/src/pages/Operations/OperationsDeploySection.tsx`, `frontend/src/pages/Operations/OperationsAdmissionSection.tsx`, `frontend/src/pages/Operations/OperationsRunnerHoursSection.tsx`, `frontend/src/pages/Operations/OperationsScheduledWorkflowsSection.tsx`, `frontend/src/pages/Operations/OperationsDiagnosticsSection.tsx`, `frontend/src/pages/Operations/OperationsDeployAuditLog.tsx`, `frontend/src/pages/Operations/deployTypes.ts`, `frontend/src/pages/Operations/diagnosticsTypes.ts`, `frontend/src/pages/Operations/OperationsPage.tsx`, `frontend/src/pages/Operations/index.ts`, `frontend/src/pages/Operations/__tests__/OperationsStatusBanner.test.tsx`, `frontend/src/pages/Operations/__tests__/OperationsDeploySection.test.tsx`, `frontend/src/pages/Operations/__tests__/OperationsAdmissionSection.test.tsx`, `frontend/src/pages/Operations/__tests__/OperationsRunnerHoursSection.test.tsx`, `frontend/src/pages/Operations/__tests__/OperationsScheduledWorkflowsSection.test.tsx`, `frontend/src/pages/Operations/__tests__/OperationsDiagnosticsSection.test.tsx`, `frontend/src/pages/Operations/__tests__/OperationsPage.test.tsx`, `frontend/src/shell/navRegistryData.ts`, `frontend/src/shell/routing.ts`, `frontend/src/shell/RoutedShell.tsx`, `frontend/src/shell/HelpAbout.tsx`, `frontend/src/shell/intro.ts`, `backend/routers/usage_metrics.py`, `frontend/src/shell/__tests__/RedirectTable.test.ts`, `frontend/src/shell/__tests__/RoutedShell.test.tsx`, `frontend/src/shell/__tests__/navRegistry.test.ts`, `frontend/src/shell/__tests__/routing.test.ts`, `frontend/src/shell/__tests__/MobileShell.test.tsx`, `frontend/src/shell/__tests__/TopToolstrip.test.tsx`, `frontend/src/pages/OverviewPage.tsx`, `frontend/src/pages/__tests__/OverviewPage.test.tsx`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (all 149 test files passed, 1,269 frontend tests passed; npm run typecheck passed with 0 errors; npm run lint passed with 0 warnings; color literal budget passed; npm run build passed; bundle budget passed; all files strictly <= 500 lines)
- **Summary:** Implemented SC-G3 Operations page merging Deployment, Fleet Orchestration, Diagnostics, Conductor, Runner Plan, and Schedules into `/fleet/operations`: (1) `OperationsStatusBanner` with quick KPI badges and 5 jump anchors (`#deploy`, `#admission`, `#runner-hours`, `#scheduled-workflows`, `#diagnostics`); (2) `OperationsDeploySection` with expected version, rollout summary, machine drift table, multi-node deploy action form, and audit log; (3) `OperationsAdmissionSection` with admission gate status (running/paused/draining), queue control buttons, capacity and work queue statistics, provider mix, budget burn, and 404 empty state; (4) `OperationsRunnerHoursSection` with desired/online/busy/offline runner metrics, schedule windows table, save/apply buttons, timer status, and config path footer; (5) `OperationsScheduledWorkflowsSection` with cron workflows table, repository badges, cron expressions, run link, search filter, and trigger CTA; (6) `OperationsDiagnosticsSection` with PID, memory MB, port, WSL status, git drift, service recovery restart with confirmation, Windows launcher generator, and API links; (7) Backwards-compatible redirects configured for `/fleet/deployment`, `/deployment`, `/t/deployment`, `/fleet/fleet-orchestration`, `/t/fleet-orchestration`, `/fleet/conductor`, `/conductor`, `/t/conductor`, `/fleet/runner-schedule`, `/runner-schedule`, `/fleet/runner-plan`, `/runner-plan`, `/t/runner-schedule`, `/work/scheduled-jobs`, `/scheduled-jobs`, `/schedules`, `/work/schedules`, `/t/scheduled-jobs`, `/settings/diagnostics`, `/diagnostics`, `/t/diagnostics` to `/fleet/operations#...` with user toast notices; (8) Recomposed shell navigation and overview deployment navigation to point to `/fleet/operations#deploy`.
- **Next step:** Shipped in PR #1426 (commit `1ce7324`).

### DL-#1428 · Restore green main: trim Mobile.tsx <= 500 lines and format api-types.ts

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1428
- **Branch:** `fix/1428-green-main`
- **Paths:** `frontend/src/pages/StaffConsole/Mobile.tsx`, `frontend/src/lib/api-types.ts`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (Mobile.tsx trimmed to 463 lines; api-types.ts formatted without duplicate blank line; vitest StaffConsole 75/75 passed; typecheck 0 errors; lint 0 warnings)
- **Summary:** Trimmed `frontend/src/pages/StaffConsole/Mobile.tsx` to 463 lines (resolving the soft-cap failure in `ci-health-check` on main) and removed duplicate newline in `frontend/src/lib/api-types.ts` before Client compatibility aliases (satisfying `generate-api:check`).
- **Next step:** Shipped in PR #1429 (commit `6a1f576`).

### DL-#1331 · SC-D8: Mobile Staff Console: roster → thread navigation, bottom composer, push deep links

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1331 (epic #1350 / umbrella #1354)
- **Branch:** `feat/1331-mobile-staff-console`
- **Paths:** `frontend/src/pages/StaffConsole/Mobile.tsx`, `frontend/src/pages/StaffConsole/mobile.css`, `frontend/src/pages/StaffConsole/index.ts`, `frontend/src/pages/StaffConsole/__tests__/Mobile.test.tsx`, `frontend/src/pages/StaffConsole/cards/cards.css`, `frontend/src/pages/StaffConsole/cards/ActionCard.tsx`, `frontend/src/pages/Staff/staffApi.ts`, `frontend/src/shell/RoutedShell.tsx`, `tests/e2e/mobile.spec.ts`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (all 75 StaffConsole unit tests passed; npm run typecheck passed 0 errors; npm run lint passed 0 warnings; pytest test_frontend_integrity passed 72/72; all files strictly <= 500 lines)
- **Summary:** Implemented SC-D8 Mobile Staff Console: (1) Full-screen mobile roster with Ask Barb top entry, role groupings, status badges, and search filtering; (2) Full-screen transition to conversation thread with `< Back to Roster` button, role header, and details sheet; (3) Safe-area aware bottom composer (`env(safe-area-inset-bottom)`) with Send and Voice input touch targets; (4) Cards adapted to narrow viewports with $\ge 44\text{px}$ touch targets on Approve/Deny buttons; (5) Push notification deep links (`?thread=<id>` and `?role=<role>`); (6) Role context bottom sheet drawer for inspecting schedule and budget; (7) Seamless mobile tab integration in `RoutedShell.tsx`.
- **Next step:** Shipped in PR #1427 (commit `94b7090`).

### DL-#1424 · Restore green main: synchronize generated API contract for SC-C5

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1333 (epic #1351 / umbrella #1354)
- **Branch:** `feat/1333-maintenance-ui`
- **Paths:** `frontend/src/pages/Fleet/fleetActions.ts`, `frontend/src/pages/Fleet/FleetRowActions.tsx`, `frontend/src/pages/Fleet/MaintenanceActionModal.tsx`, `frontend/src/pages/Fleet/FleetMachinesSection.tsx`, `frontend/src/pages/Fleet/FleetRunnersSection.tsx`, `frontend/src/pages/Fleet/index.ts`, `frontend/src/pages/OverviewPage.tsx`, `frontend/src/pages/StaffConsole/cards/ActionCard.tsx`, `frontend/src/pages/StaffConsole/cards/cardTypes.ts`, `backend/staff/router_models.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (vitest 72/72 tests passing across Fleet and cards; npm run typecheck 0 errors; npm run lint 0 warnings; ruff check clean; pytest router & maintenance 12/12 passing; test_color_literal_budget passing; all files <= 500 lines)
- **Summary:** Implemented SC-E6 Maintenance row actions on the Fleet page: (1) Added accessible `FleetRowActions` dropdown menu for machines and runners covering 5 actions ("Bring online", "Take offline", "Restart", "Compact disk", "Diagnose"); (2) Followed Owner decision (2026-09-23) routing mutating actions through Barb to Maintenance, and read-only actions (Diagnose) directly to Maintenance; (3) Added dry-run display with planned steps, verification confirmations, and Barb routing badges to `ActionCard`; (4) Built `MaintenanceActionModal` presenting pre-filled action card with dry-run shown, executing upon approval, verifying postcondition state cleanly, and refreshing fleet data; (5) Added maintenance action keyword rules to `router_models.py`.
- **Next step:** Push branch, open PR with Fixes #1333, enable auto-merge, monitor CI until merged, release lease, and clean up.

### DL-#1424 · Restore green main: synchronize generated API contract for SC-C5

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1424
- **Branch:** `fix/1424-green-main`
- **Paths:** `frontend/src/lib/openapi.json`, `frontend/src/lib/api-types.ts`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (pytest tests/frontend/test_api_generation_contract.py and test_staff_contracts.py passed 10/10; npm run typecheck 0 errors; npm run lint 0 warnings)
- **Summary:** Synchronized generated OpenAPI schema (`openapi.json`) and TypeScript client types (`api-types.ts`) following SC-C5 merge (`/api/v1/staff/briefing` and updated `/api/v1/staff/inbox`), resolving the failing `Verify generated API contract types` step in `Frontend Tests` on `main`.
- **Next step:** Shipped in PR #1425 (commit `c2292e3`).

### DL-#1328 · SC-C5: "Waiting on you" inbox and Barb briefings inside dashboard

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1328 (epic #1349 / umbrella #1354)
- **Branch:** `feat/1328-waiting-on-you-inbox`
- **Paths:** `backend/staff/inbox.py`, `backend/routers/staff_inbox.py`, `backend/routers/staff_threads.py`, `backend/server.py`, `backend/push.py`, `frontend/src/pages/Staff/inboxTypes.ts`, `frontend/src/pages/Staff/InboxPanel.tsx`, `frontend/src/pages/Staff/StaffPage.tsx`, `frontend/src/pages/Staff/staffApi.ts`, `frontend/src/pages/Staff/index.ts`, `frontend/src/pages/StaffConsole/index.ts`, `frontend/src/pages/StaffConsole/__tests__/InboxPanel.test.tsx`, `tests/api/test_staff_inbox.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (pytest tests/api/test_staff_inbox.py passed 4/4; test_staff_threads_api.py passed 11/11; vitest InboxPanel.test.tsx passed 6/6; StaffConsole suite passed 63/63; npm run typecheck 0 errors; npm run lint 0 warnings; ruff & mypy clean; all files <= 500 lines)
- **Summary:** Implemented SC-C5 Waiting on you inbox and Barb briefings inside dashboard: (1) `backend/staff/inbox.py` multi-source aggregation (`_collect_approvals`, `_collect_needs_input`, `_collect_escalations`, `_collect_project_decisions`, `_collect_board_proposals`, `_collect_auth_sign_ins`) with per-source fault isolation returning `status: "unavailable"` and HTTP 200; (2) Scheduled/on-demand Markdown briefing generation (`POST /api/v1/staff/briefing`) posted to Barb's thread with SC-A8 audit logging; (3) Web push (`staff.escalation`) for critical escalations with thread deep links; (4) Frontend `InboxPanel` with severity badges, category pills, degraded source banner, briefing trigger, and deep linking, embedded at the top of `StaffPage`.
- **Next step:** Shipped in PR #1422 (commit `c9e558f`).

### DL-#1324 · SC-G2: One Fleet page: merge Machines, Runner Audit and Event Log into Fleet

- **State:** shipped
- **Owner:** local
- **Issue:** #1324 (epic #1353 / umbrella #1354)
- **Branch:** `feat/1324-one-fleet-page`
- **Paths:** `frontend/src/pages/Fleet/FleetStatusBanner.tsx`, `frontend/src/pages/Fleet/FleetMachinesSection.tsx`, `frontend/src/pages/Fleet/FleetRunnersSection.tsx`, `frontend/src/pages/Fleet/FleetAlertsSection.tsx`, `frontend/src/pages/Fleet/FleetEventsSection.tsx`, `frontend/src/pages/Fleet/index.ts`, `frontend/src/pages/OverviewPage.tsx`, `frontend/src/shell/navRegistryData.ts`, `frontend/src/shell/routing.ts`, `frontend/src/shell/__tests__/RedirectTable.test.ts`, `frontend/src/shell/__tests__/RoutedShell.test.tsx`, `frontend/src/shell/__tests__/TopToolstrip.test.tsx`, `frontend/src/shell/__tests__/navRegistry.test.ts`, `frontend/src/shell/__tests__/routing.test.ts`, `frontend/src/pages/Fleet/__tests__/FleetStatusBanner.test.tsx`, `frontend/src/pages/Fleet/__tests__/FleetMachinesSection.test.tsx`, `frontend/src/pages/Fleet/__tests__/FleetRunnersSection.test.tsx`, `frontend/src/pages/Fleet/__tests__/FleetAlertsSection.test.tsx`, `frontend/src/pages/Fleet/__tests__/FleetEventsSection.test.tsx`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (all 142 test files passed, 1,245 frontend tests passed; npm run typecheck passed with 0 errors; npm run lint passed with 0 warnings; color literal budget passed; npm run build passed; perf budget passed; all files strictly <= 500 lines)
- **Summary:** Implemented SC-G2 One Fleet page merging Machines, Runner Audit, and Event Log into Fleet: (1) `FleetStatusBanner` complying with SC-A2 status honesty, tri-state health, KPI summary strip, and jump anchors (`#machines`, `#runners`, `#alerts`, `#events`); (2) `FleetMachinesSection` providing single unified machines table with expandable telemetry (WSL, CPU, RAM, storage devices, runner pool) and "Ask Maintenance" button for SC-E6; (3) `FleetRunnersSection` providing status filter pills, fleet control buttons (Start All, Stop All), runner table with labels, current task links, and Maintenance actions; (4) `FleetAlertsSection` surfacing active fleet alerts and hosted-runner billing violations audit table from `runnerAudit` with refresh button and empty state; (5) `FleetEventsSection` displaying recent durable fleet events with level filters and independent error state; (6) `OverviewPage.tsx` recomposed with section-level failure isolation and smooth hash scrolling; (7) Backwards-compatible redirects configured for `/fleet/machines`, `/machines`, `/t/machines` to `/fleet#machines`, `/fleet/runner-audit`, `/runner-audit`, `/t/runner-audit` to `/fleet#alerts`, and `/fleet/events`, `/events`, `/t/events` to `/fleet#events` with user toast notices.
- **Next step:** Shipped in PR #1421.

### DL-#1319 · SC-D5: Action, run, hand-off, and review cards embedded in conversation threads

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1319 (epic #1350 / umbrella #1354)
- **Branch:** `feat/1319-thread-cards`
- **Paths:** `frontend/src/pages/StaffConsole/cards/cardTypes.ts`, `frontend/src/pages/StaffConsole/cards/ActionCard.tsx`, `frontend/src/pages/StaffConsole/cards/RunCard.tsx`, `frontend/src/pages/StaffConsole/cards/HandoffCard.tsx`, `frontend/src/pages/StaffConsole/cards/ReviewCard.tsx`, `frontend/src/pages/StaffConsole/cards/ErrorCard.tsx`, `frontend/src/pages/StaffConsole/cards/cards.css`, `frontend/src/pages/StaffConsole/cards/index.ts`, `frontend/src/pages/StaffConsole/cards/__tests__/ActionCard.test.tsx`, `frontend/src/pages/StaffConsole/cards/__tests__/RunCard.test.tsx`, `frontend/src/pages/StaffConsole/cards/__tests__/HandoffCard.test.tsx`, `frontend/src/pages/StaffConsole/cards/__tests__/ReviewCard.test.tsx`, `frontend/src/pages/StaffConsole/cards/__tests__/ErrorCard.test.tsx`, `frontend/src/pages/StaffConsole/MessageItem.tsx`, `frontend/src/pages/StaffConsole/Thread.tsx`, `frontend/src/pages/StaffConsole/threadTypes.ts`, `frontend/src/pages/StaffConsole/threadMarkdown.tsx`, `frontend/src/pages/StaffConsole/index.ts`, `frontend/src/pages/StaffConsole/__tests__/Thread.test.tsx`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (all 55 StaffConsole unit tests passed; npm run typecheck passed with 0 errors; npm run lint passed with 0 warnings; pytest test_frontend_integrity passed 72 tests; all touched files strictly <= 500 lines)
- **Summary:** Implemented SC-D5 Action, run, hand-off, and review cards embedded in conversation threads: (1) `ActionCard`: action target, risk badge (`read`/`low`/`medium`/`high`/`critical`/`owner-only`), expandable parameters view, double-click idempotency protection executing once, decision history display, and stale proposal expiration guard (24h limit) disabling actions; (2) `RunCard`: live status indicator (`queued`/`running`/`completed`/`failed`/`cancelled`), node host, provider model, elapsed duration, expandable log tail with toggle, Cancel CTA, and deep links to run page and GitHub PR; (3) `HandoffCard`: routing transition ("Barb → Specialist"), rationale, and interactive specialist re-route selection; (4) `ReviewCard`: PR reference, verdict badge (`APPROVED`/`CHANGES_REQUESTED`/`COMMENTED`), review summary, and key findings list; (5) `ErrorCard`: classified failure display mapping `failure_class` to plain-language cause, highlighted remediation instructions, node badge, and retry CTA; (6) `MessageItem` & `Thread` routing: dynamic card dispatch based on message kind with full callback forwarding.
- **Next step:** Shipped in PR #1419.

### DL-#1318 · SC-D4: Thread view and composer: streaming markdown, @mentions, slash commands, reliable send

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1318 (epic #1350 / umbrella #1354)
- **Branch:** `feat/1318-thread-composer`
- **Paths:** `frontend/src/pages/StaffConsole/threadTypes.ts`, `frontend/src/pages/StaffConsole/threadMarkdown.tsx`, `frontend/src/pages/StaffConsole/composerUtils.ts`, `frontend/src/pages/StaffConsole/ComposerAutocompletes.tsx`, `frontend/src/pages/StaffConsole/Composer.tsx`, `frontend/src/pages/StaffConsole/MessageItem.tsx`, `frontend/src/pages/StaffConsole/Thread.tsx`, `frontend/src/pages/StaffConsole/useThreadStream.ts`, `frontend/src/pages/StaffConsole/index.ts`, `frontend/src/pages/StaffConsole/__tests__/threadMarkdown.test.tsx`, `frontend/src/pages/StaffConsole/__tests__/Composer.test.tsx`, `frontend/src/pages/StaffConsole/__tests__/Thread.test.tsx`, `frontend/src/pages/StaffConsole/__tests__/useThreadStream.test.ts`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (all 34 StaffConsole tests passed; npm run typecheck passed with 0 errors; ruff check passed; all files strictly <= 500 lines)
- **Summary:** Implemented SC-D4 Thread view and Composer: (1) Sanitized markdown rendering (`ThreadMarkdown`) with code block copying, link previews for issues/PRs/runs, and strict XSS protection with DOMPurify; (2) Keyboard-first Composer (`Composer`, `ComposerAutocompletes`, `composerUtils`) with @mentions role auto-complete, slash commands (`/dispatch`, `/review`, `/status`, `/hold`, `/brief`), voice input integration, reliable send with idempotent retry (retaining same Idempotency-Key), and per-thread draft persistence; (3) Thread view (`Thread`, `MessageItem`) with date separators across day boundaries, jump-to-unread button, streaming token deltas with stop button, classified error cards with remediation, and SSE reconnection management (`useThreadStream`).
- **Next step:** Shipped in PR #1413 / #1414.

### DL-#1329 · SC-C6: Barb availability: reserved capacity, provider fallback, acknowledgement SLA and degraded mode

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1329 (epic #1349 / umbrella #1354)
- **Branch:** `feat/1329-barb-availability`
- **Paths:** `backend/staff/availability.py`, `backend/staff/chat_pool.py`, `backend/staff/chat.py`, `backend/routers/staff_threads.py`, `backend/staff/thread_helpers.py`, `backend/health.py`, `backend/staff/fleet.py`, `tests/unit/test_staff_availability.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (all 10 availability tests passed; all 13 chat unit tests passed; all 10 thread API tests passed; mypy passed 0 errors; ruff check passed; black formatting verified; all touched files strictly <= 500 lines)
- **Summary:** Implemented SC-C6 Barb availability guarantees: (1) Reserved chat capacity for Barb independent of heavy work runs via dedicated `ChatConcurrencyPool` and `StaffRunner._sema` isolation; (2) Provider fallback chain (`claude` -> `codex` -> `claude-ollama` -> `ollama`) with health probes and runtime error fallback; active provider and fallback count recorded in thread and message metadata; (3) Fast acknowledgment SLA (< 3 s) system messages ("On it: routing to ...") emitted immediately upon message submission; (4) Degraded mode when all LLM providers fail or are disabled: deterministic rule-based routing, queued follow-up work item in `WorkItemStore`, clearly labeled explanatory text; (5) Availability metrics (`ack_latency_ms`, `first_token_latency_ms`, `fallback_count`, `degraded_mode_count`) tracked in `AvailabilityMetrics` and exposed on the Board and in `/api/health`.
- **Next step:** Shipped in PR #1410.

### DL-#1317 · SC-D3: Staff roster sidebar: grouped roles, Auto (Barb) entry, status, unread counts, search and pinning

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1317 (epic #1350 / umbrella #1354)
- **Branch:** `feat/1317-staff-roster-sidebar`
- **Paths:** `frontend/src/pages/StaffConsole/types.ts`, `frontend/src/pages/StaffConsole/rosterUtils.ts`, `frontend/src/pages/StaffConsole/RosterRow.tsx`, `frontend/src/pages/StaffConsole/RosterGroup.tsx`, `frontend/src/pages/StaffConsole/Roster.tsx`, `frontend/src/pages/StaffConsole/index.ts`, `frontend/src/pages/StaffConsole/__tests__/Roster.test.tsx`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (`npx vitest run frontend/src/pages/StaffConsole/__tests__/Roster.test.tsx` 15 passed; `npm run typecheck` 0 errors; `npm run lint` 0 warnings; all files strictly <= 500 lines)
- **Summary:** Implemented the Staff Console roster sidebar component (`frontend/src/pages/StaffConsole/Roster.tsx` and modular primitives). Features dedicated top entry for 'Ask Barb (auto-route)'; 4-tier categorical role grouping per SC-D1 (Leadership, Project Managers, Specialists, Operations); live operational status indicators (`idle`, `working`, `needs_you`, `unavailable`, `invalid`) with tooltip explanations for operational blocks (holds, budget limits, unauthenticated provider) and invalid role definitions; real-time search filtering across name, title, and mandate summary; role pinning with local storage persistence and dedicated Pinned section; collapsible group sections; unread count badge and relative-age message preview; network failure fallback retaining previous roster with visible 'Stale Data' badge; and full keyboard navigation (`ArrowUp`/`ArrowDown`, `Enter`/`Space`). Covered by TDD unit tests in `Roster.test.tsx`.
- **Next step:** Shipped in PR #1411.

### DL-#1301 · SC-D1: UX spec: Staff Console as the landing page and a four-area information architecture

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1301 (epic #1348 / umbrella #1354)
- **Branch:** `feat/1301-staff-console-ux-spec`
- **PR:** #1405
- **Paths:** `docs/design/staff-console.md`, `tests/test_staff_console_design_spec.py`, `backend/staff/chat.py`, `tests/api/test_staff_spend_and_rate_limits.py`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (shipped in PR #1405 via commit 508f4c5; CI green on main)
- **Summary:** Authored comprehensive UX specification and interaction contract for Staff Console (`docs/design/staff-console.md`) establishing Staff Console as the primary landing page across a four-area information architecture (Staff, Work, Fleet, Settings). Defines 6 core design principles, ASCII wireframes for desktop (three-pane), tablet (collapsible drawer), mobile (single-pane bottom navigation), and first-run empty states. Details structured inline card interactions for action approvals, run records, and error remediation with standardized action verbs and complete failure/lifecycle state catalogue. Covered by TDD test suite `tests/test_staff_console_design_spec.py`.
- **Next step:** Shipped in PR #1405.

### DL-#1326 · SC-G4: Merge the duplicate Reports and Analysis tabs into one Insights section

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1326 (epic #1353 / umbrella #1354)
- **Branch:** `feat/1326-merge-reports-analysis`
- **PR:** #1409
- **Paths:** `frontend/src/shell/navRegistryData.ts`, `frontend/src/shell/routing.ts`, `frontend/src/shell/RoutedShell.tsx`, `frontend/src/pages/Analysis.tsx`, `frontend/src/pages/Diagnostics.tsx`, `frontend/src/lib/analysisTabs.ts`, `frontend/src/shell/__tests__/RedirectTable.test.ts`, `frontend/src/shell/__tests__/navRegistry.test.ts`, `frontend/src/pages/__tests__/Diagnostics.test.tsx`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (shipped in PR #1409; merged into main)
- **Summary:** Merged duplicate Reports and Analysis navigation tabs into a unified "Insights" section (`tabId: "insights"`) under Fleet navigation (`/fleet/insights`). Configured backward-compatible redirects from `/t/reports`, `/t/analysis`, `/fleet/reports`, and `/fleet/analysis` to `/fleet/insights` with toast notifications. Relocated Web Vitals metric inspection from AnalysisTab to DiagnosticsTab as a dedicated card.
- **Next step:** Shipped in PR #1409.

### DL-#1407 · CI: Restore green main across frontend integrity checks and generated API contract

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1407
- **Branch:** `fix/1407-green-main`
- **PR:** #1408
- **Paths:** `frontend/src/lib/openapi.json`, `frontend/src/lib/api-types.ts`, `frontend/src/main.tsx`, `tests/test_frontend_integrity.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (shipped in PR #1408; CI green on main)
- **Summary:** Restores green main across frontend integrity checks and API contract tests. Synchronized OpenAPI schema (`openapi.json`) and TypeScript client types (`api-types.ts`) with newly added SC-C2 Barb routing endpoints, preserving `ValidationError.ctx` and `ValidationError.input` for Python 3.11 CI compatibility. Updated `tests/test_frontend_integrity.py` to also inspect `navRegistryData.ts` when auditing native desktop route content, accommodating modular navigation registry files under line-length caps. Maintained `PushSettings` reference in `frontend/src/main.tsx` for integrity assertion.
- **Next step:** Shipped in PR #1408.

### DL-#1309 · SC-D2: Shell restructure: Staff Console as default route, four-area navigation, redirects for old tabs

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1309 (epic #1348 / umbrella #1354)
- **Branch:** `feat/1309-shell-restructure`
- **PR:** #1406
- **Paths:** `frontend/src/main.tsx`, `frontend/src/shell/NotFoundPanel.tsx`, `frontend/src/shell/navRegistryData.ts`, `frontend/src/shell/navRegistry.ts`, `frontend/src/shell/routing.ts`, `frontend/src/shell/DesktopShell.tsx`, `frontend/src/shell/MobileShell.tsx`, `frontend/src/shell/RoutedShell.tsx`, `frontend/src/primitives/CommandPalette.tsx`, `frontend/src/shell/__tests__/RedirectTable.test.ts`, `frontend/src/shell/__tests__/NotFoundPanel.test.tsx`, `frontend/src/shell/__tests__/navRegistry.test.ts`, `frontend/src/shell/__tests__/routing.test.ts`, `frontend/src/shell/__tests__/DesktopShell.test.tsx`, `frontend/src/shell/__tests__/MobileShell.test.tsx`, `frontend/src/shell/__tests__/RoutedShell.test.tsx`, `frontend/src/shell/__tests__/TopToolstrip.test.tsx`, `frontend/src/pages/__tests__/FleetCommand.test.tsx`, `frontend/src/pages/__tests__/OverviewPage.test.tsx`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (shipped in PR #1406; all 126 Vitest test suites, 1,158 tests passed; TypeScript check 0 errors; ESLint 0 warnings; all touched files strictly <= 500 lines)
- **Summary:** Restructured frontend shell navigation for SC-D2: default route `/` maps to Staff Console (`staff`); four-area navigation in Sidebar and Registry (`staff`, `work`, `fleet`, `settings`) with canonical secondary routes (`/fleet/:tabId`, `/work/:tabId`, `/staff/:tabId`, `/settings/:tabId`); old tab routes `/t/:tabId` redirect via `getTabRedirect` with one-time 'moved to' toast notification; top toolstrip replaced by CommandPalette trigger (Ctrl/Cmd+K); mobile bottom bar updated to Staff/Work/Fleet/More; unknown routes render visibly via `NotFoundPanel` without dropping shell chrome; split `navRegistry.ts` into `navRegistryData.ts` and `navRegistry.ts` to respect <= 500 lines soft-cap.
- **Next step:** Shipped in PR #1406.

### DL-#1315 · SC-C2: Barb routing: auto-select the right role(s) for a request, show decision, allow override

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1315 (epic #1349 / umbrella #1354)
- **Branch:** `feat/1315-barb-routing`
- **PR:** #1403
- **Paths:** `backend/staff/router.py`, `backend/staff/router_models.py`, `backend/routers/staff_routing.py`, `backend/server.py`, `tests/unit/test_staff_router.py`, `tests/api/test_staff_routing_api.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (shipped in PR #1403; CI green)
- **Summary:** Implemented Barb two-stage request router (`backend/staff/router.py`, `backend/staff/router_models.py`) and FastAPI endpoints (`backend/routers/staff_routing.py`). Stage 1 evaluates deterministic pre-router rules (explicit @mentions, /role commands, Barb self-handling keywords, specialist role capability keywords, code change detection). Stage 2 uses roster metadata with quick fallback mode when LLM is unavailable. Prompts below confidence threshold ask a single clarifying question rather than guessing. Handoff execution posts structured handoff cards ('Barb → Role: reason'), creates/resumes destination threads, links WorkItemStore tracked work items (SC-C3), routes code modifications to Code Request pipeline (#1279), and records owner overrides with auditable routing feedback (SC-C7).
- **Next step:** Shipped in PR #1403.

### DL-#1322 · SC-E5: Stalled-job detection and remediation playbooks for the Maintenance role

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1322 (epic #1351 / umbrella #1354)
- **Branch:** `feat/1322-stalled-job-detection`
- **PR:** #1402
- **Paths:** `backend/staff/maintenance_detect.py`, `backend/staff/maintenance.py`, `backend/staff/actions.py`, `backend/routers/staff_proposals.py`, `tests/unit/test_staff_maintenance_detect.py`, `tests/api/test_staff_maintenance_detect_api.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (`pytest tests/unit/test_staff_maintenance_detect.py` 9 passed; `pytest tests/api/test_staff_maintenance_detect_api.py` 2 passed; full maintenance suite 39 passed; ruff clean; mypy 0 errors; all files <= 500 lines; CI passed green)
- **Summary:** Implemented autonomous stalled-job detection and remediation playbooks (`backend/staff/maintenance_detect.py`) for the Fleet Maintenance role. Detectors include queued too long with idle matching runners, running past p95 \* 3, runner online but listener log stale, runner offline with assigned job, and ghost runner registrations. Automatically executes low-risk remediations (cancel and rerun, restart wedged listener) and generates action proposals in the Maintenance thread for medium/high-risk actions (run cancel, runner remove) awaiting operator approval. Isolates exceptions per detector, logs SC-A8 audit records, and exposes `POST /api/v1/staff/maintenance/detect-stalled`.
- **Next step:** Shipped in PR #1402.

### DL-#1307 · SC-B4: Chat-turn execution path: fast replies with per-provider session resume, no worktree

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1307 (epic #1346 / umbrella #1354)
- **Branch:** `feat/1307-chat-turn-execution-path`
- **PR:** #1398
- **Paths:** `backend/staff/chat.py`, `backend/staff/adapters.py`, `backend/staff/conversations.py`, `backend/staff/conversation_models.py`, `backend/staff/conversation_migrations.py`, `backend/routers/staff_threads.py`, `tests/unit/test_staff_chat.py`, `tests/api/test_staff_chat_turns.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (shipped in PR #1398)
- **Summary:** Implemented conversational chat turn execution engine (`backend/staff/chat.py`) in read-only scratch directories without git worktree checkout. Added provider session extraction and persistence (`meta.provider_sessions`), multi-turn session resumption (`--resume`), fallback to history replay under 4000-token budget, chat concurrency pool with reserved slots for Barb (SC-C6), reply contract parsing with action proposal creation, and background turn execution on message post.
- **Next step:** None (shipped in PR #1398).

### DL-#1336 · SC-F7: Rate limits and spend guards on staff conversation and dispatch APIs

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1336 (epic #1352 / umbrella #1354)
- **Branch:** `feat/1336-rate-limits-spend-guards`
- **PR:** #1399
- **Paths:** `backend/staff/rate_limit.py`, `backend/staff/budget.py`, `backend/staff/loop_guard.py`, `backend/staff/conversation_models.py`, `backend/routers/staff_threads.py`, `backend/routers/staff.py`, `SPEC.md`, `tests/api/test_staff_spend_and_rate_limits.py`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (shipped in PR #1399)
- **Summary:** Implemented per-principal token-bucket limits on message send (30/min) and dispatch (10/hour) returning 429 with Retry-After and fail-open/fail-closed storage error handling. Extended BudgetGuard to track chat turn spend against usd_per_day, producing fixed system messages upon exhaustion and notifying Barb. Added LoopGuard detecting > N consecutive agent turns without user messages to pause threads and request owner input.
- **Next step:** None (shipped in PR #1399).

### DL-#1321 · SC-E3: Maintenance action catalogue: typed, allowlisted fleet operations with preflight, dry-run and verification

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1321 (epic #1351 / umbrella #1354)
- **Branch:** `feat/1321-maintenance-catalogue`
- **PR:** #1400
- **Paths:** `backend/staff/maintenance.py`, `backend/staff/actions.py`, `backend/staff/action_executors.py`, `tests/unit/test_staff_maintenance.py`, `tests/api/test_staff_maintenance_api.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (shipped in PR #1400)
- **Summary:** Implemented typed maintenance operations with safety rails (`backend/staff/maintenance.py`) integrated into `ActionRegistry` (`backend/staff/actions.py`, `backend/staff/action_executors.py`). Registered 13 maintenance actions: `maintenance.runner_start`, `maintenance.runner_stop`, `maintenance.runner_restart`, `maintenance.runner_drain`, `maintenance.group_start`, `maintenance.group_stop`, `maintenance.fleet_control`, `maintenance.queue_purge_stale`, `maintenance.run_cancel`, `maintenance.run_rerun`, `maintenance.trim_worktrees`, `maintenance.vacuum_sqlite`, and `maintenance.diagnose`. Enforced preflight checks (busy runners require drain before stop/restart unless `force=True`), blast-radius bounds (`max_count <= 10`), single-host restriction on disruptive operations (`fleet_control` disallows `host="all"`), cooldown tracker preventing rapid consecutive operations per action and target, dry-run planning returning detailed action plans without side effects, per-target partial failure aggregation, and SC-A8 SQLite audit logging. Verification handlers check real runner status (`stopped`, `online`, `active`) and raise `MaintenanceVerificationError` on mismatch.
- **Next step:** None (shipped in PR #1400).

### DL-#1313 · SC-B6: Action proposals from conversations with risk-based approval gates

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1313 (epic #1348 / umbrella #1354)
- **Branch:** `feat/1313-action-proposals`
- **PR:** #1396
- **Paths:** `backend/staff/actions.py`, `backend/staff/action_executors.py`, `backend/staff/conversation_models.py`, `backend/staff/conversations.py`, `backend/routers/staff_proposals.py`, `backend/routers/assistant.py`, `tests/unit/test_staff_actions.py`, `tests/api/test_staff_proposals_api.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (shipped in PR #1396)
- **Summary:** Replaced legacy stubs with unified `ActionRegistry` (`staff/actions.py`, `staff/action_executors.py`); approval policies (`read`/`low` auto-execute, `medium` operator approve with `staff.approve` scope, `high`/`owner-only` owner approve); 24h proposal expiry and terminal replay protection; role permission gating (unauthorized roles rejected with 403 Forbidden); post-execution verifiers validating actual state changes; dispatched runs and action outcomes post `action_result` and `run_card` messages back to conversation threads (SC-B7), fully audited in `staff_audit` (SC-A8). Mounted REST endpoints in `backend/routers/staff_proposals.py` under `/api/v1/staff`: `GET /api/v1/staff/actions`, `GET /api/v1/staff/actions/{name}`, `POST /api/v1/staff/proposals`, `POST /api/v1/staff/proposals/{id}/decide` (with immediate execution option), and `POST /api/v1/staff/proposals/{id}/execute`.
- **Next step:** None (shipped in PR #1396).

### DL-#1334 · SC-F5: External Agent Connection Guides & Troubleshooting

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1334 (epic #1352 / umbrella #1354)
- **Branch:** `docs/1334-agent-connection-guides`
- **PR:** #1397
- **Paths:** `docs/agents/claude.md`, `docs/agents/codex.md`, `docs/agents/grok.md`, `docs/agents/connect.md`, `docs/staff-hub.md`, `SPEC.md`, `tests/test_agent_connection_docs.py`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25 (shipped in PR #1397)
- **Summary:** Authored external client connection guides for Claude Code / Claude Cowork (`docs/agents/claude.md`), Codex CLI (`docs/agents/codex.md`), and Grok Bot (`docs/agents/grok.md`). Updated `docs/agents/connect.md` with client guide navigation index, complete 25-tool fleet MCP reference table, and SC-F3 classified error troubleshooting guide. Added cross-references in `docs/staff-hub.md` and updated `SPEC.md` SC-F5 specification. Added automated documentation validation tests.
- **Next step:** None (shipped in PR #1397).

### DL-#1323 · SC-F4: Fleet MCP tools for staff conversations, work items, approvals and cancel

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1323 (epic #1352 / umbrella #1354)
- **Branch:** `feat/1323-fleet-mcp-staff`
- **PR:** #1395
- **Paths:** `clients/fleet/fleet_client.py`, `clients/fleet/fleet_validators.py`, `clients/fleet/fleet_tools.py`, `clients/fleet/fleet_mcp.py`, `backend/routers/staff_proposals.py`, `backend/routers/staff_threads.py`, `backend/server.py`, `tests/api/test_staff_proposals_api.py`, `tests/clients/test_fleet_client.py`, `tests/clients/test_fleet_mcp.py`, `tests/clients/test_fleet_cli.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (shipped in PR #1395)
- **Summary:** Exposed 9 staff tools in `fleet_mcp` and `fleetctl`: `staff_threads_list`, `staff_thread_open`, `staff_message_send`, `staff_thread_read`, `staff_thread_wait`, `staff_run_cancel`, `staff_work_items`, `staff_approvals_list`, `staff_approval_decide`. Standardized SC-F3 error envelope for tool errors and implemented idempotent request retries for network/5xx errors on idempotent calls. Added action proposal review and decision endpoints `GET/POST /api/v1/staff/proposals`.
- **Next step:** None (shipped in PR #1395).

### DL-#1316 · SC-C3: Work-item ledger: every request Barb (or anyone) dispatches is tracked to a terminal state

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1316 (epic #1349 / umbrella #1354)
- **Branch:** `feat/1316-work-item-ledger`
- **PR:** #1394
- **Paths:** `backend/staff/work_items.py`, `backend/routers/staff_work_items.py`, `backend/staff/run_link.py`, `backend/staff/audit.py`, `backend/server.py`, `tests/api/test_staff_work_items.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (shipped in PR #1394)
- **Summary:** Implemented durable WorkItem ledger with SQLite WAL persistence in `staff_runs.sqlite3`. Supports state transitions (`open`, `in_progress`, `waiting_on_user`, `waiting_on_ci`, `blocked`, `done`, `cancelled`, `escalated`), SLA overdue detection, links to runs, issues, PRs, and code requests. State transitions are audited in `staff_audit` (SC-A8). Run status updates (`run_link.py`) automatically transition linked work items. Exposed REST endpoints `POST /api/v1/staff/work-items`, `GET /api/v1/staff/work-items` (filters: `mine`, `overdue`, `waiting_on_me`, `state`, `thread_id`, cursor pagination), `GET /api/v1/staff/work-items/{id}`, and `PATCH /api/v1/staff/work-items/{id}`.
- **Next step:** None (shipped in PR #1394).

### DL-#1314 · SC-B7: Link runs to threads, post progress back, answer needs-input questions, and proxy run streams across nodes

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1314 (epic #1348 / umbrella #1354)
- **Branch:** `feat/1314-link-runs-to-threads`
- **PR:** #1393
- **Paths:** `backend/routers/staff.py`, `backend/routers/staff_threads.py`, `backend/staff/run_link.py`, `backend/staff/remote_runs.py`, `backend/staff/plan.py`, `backend/staff/store.py`, `backend/staff/runner.py`, `backend/staff/classifier.py`, `backend/staff/thread_bus.py`, `tests/api/test_staff_thread_runs.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (shipped in PR #1393)
- **Summary:** Enabled background staff runs across the fleet to link with conversation threads via `thread_id` and `work_item_id`. Transitions post run cards to threads and publish across `ThreadEventBus`. Cross-node run detail, cancel, and SSE streams proxied with on-behalf-of identity. Unattended agents stopping with questions transition to `needs_input` when threaded, continuing upon answer.
- **Next step:** None (shipped in PR #1393).

### DL-#1306 · SC-B3: Conversation API: threads, messages, streaming replies (SSE with resume) and unread state

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1306 (epic #1348 / umbrella #1354)
- **Branch:** `feat/1306-conversation-api`
- **PR:** #1391
- **Paths:** `backend/routers/staff_threads.py`, `backend/staff/thread_bus.py`, `backend/staff/conversations.py`, `backend/server.py`, `docs/api/staff-v1.md`, `frontend/src/lib/openapi.json`, `frontend/src/lib/api-types.ts`, `tests/api/test_staff_threads_api.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (shipped in PR #1391)
- **Summary:** REST and SSE conversation endpoints mounted under `/api/v1/staff/threads` and `/api/v1/staff/inbox`. Creates direct, group, or auto-routed threads (kind `auto` routes target role to Barb). Keyset cursor pagination and filtering by participant role, status, and unread. Message submission requires `Idempotency-Key` and returns 202 Accepted with user message record and pending reply placeholder record. Duplicate submissions replay with `Idempotent-Replay: true`. In-memory `ThreadEventBus` (`backend/staff/thread_bus.py`) publishes live token/message/proposal/run_card events over SSE (`/api/v1/staff/threads/{id}/stream`) with `Last-Event-ID` sequential replay from SQLite message log, 15s heartbeats, and client disconnect handling. Unread state tracking via `/threads/{id}/read` and inbox rollup. Fail-closed 503 on degraded conversation store. All modules strictly <= 500 lines.
- **Next step:** None (shipped in PR #1391).

### DL-#1312 · SC-F3: Versioned public staff API (/api/v1/staff) with error envelope, idempotency and pagination

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1312 (epic #1352 / umbrella #1354)
- **Branch:** `feat/1312-versioned-staff-api`
- **PR:** #1387
- **Paths:** `backend/routers/staff_v1.py`, `backend/staff/v1_envelope.py`, `backend/staff/idempotency.py`, `backend/staff/pagination.py`, `backend/server.py`, `docs/api/staff-v1.md`, `frontend/src/pages/Staff/staffApi.ts`, `frontend/src/lib/api.ts`, `frontend/src/pages/Projects/types.ts`, `frontend/src/pages/Projects/ProjectCard.tsx`, `tests/api/test_staff_v1_api.py`, `tests/unit/test_staff_v1_primitives.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (shipped in PR #1387)
- **Summary:** Versioned public staff API mounted under `/api/v1/staff` with standard error envelopes `{error: {code, message, retryable, hint, request_id}}` on all 4xx/5xx responses. Legacy `/api/staff` aliases carry RFC 8594 `Deprecation`, `Sunset`, and `Link` headers. 24h SQLite WAL idempotency ledger (`backend/staff/idempotency.py`) requiring `Idempotency-Key` on mutating routes with replay headers and fail-closed 503 behavior on persistence failures. Keyset cursor pagination (`backend/staff/pagination.py`) for runs and audit. Published `docs/api/staff-v1.md`. Migrated UI to `/api/v1/staff` exclusively.
- **Next step:** None (shipped in PR #1387).

### DL-#1305 · SC-B2: Thread, message and action-proposal store with migrations

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1305 (epic #1348 / umbrella #1354)
- **Branch:** `feat/1305-conversations-store`
- **PR:** #1385
- **Paths:** `backend/staff/conversations.py`, `backend/staff/conversation_models.py`, `backend/staff/conversation_migrations.py`, `backend/staff/conversation_proposals.py`, `backend/staff/redaction.py`, `backend/staff/audit.py`, `tests/unit/test_conversations_store.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (shipped in PR #1385)
- **Summary:** Persist conversations on conversation hub in `staff_runs.sqlite3` with SQLite WAL mode and `threading.RLock()`. Tables `threads`, `messages`, `action_proposals`, and `schema_migrations`. Forward-only migrations at startup with automatic timestamped pre-migration backups. Fail-safe degraded status and banner on migration error. Pre-write redaction hook for secret tokens and RFC 1918 / RFC 6598 private LAN IPv4 addresses. Monotonic message sequencing with idempotency deduplication. Action proposal state machine with SC-A8 auditing. All modules strictly <= 500 lines.
- **Next step:** None (shipped in PR #1385).

### DL-#1304 · SC-A9: One frontend data layer for staff and conversation data (React Query)

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1304 (epic #1347 / umbrella #1354)
- **Branch:** `feat/1304-react-query-datalayer`
- **PR:** #1380
- **Paths:** `frontend/src/lib/api.ts`, `frontend/src/hooks/usePollingQueries.ts`, `frontend/src/hooks/useStaffQueries.ts`, `frontend/src/primitives/ConnectionIndicator.tsx`, `frontend/src/hooks/useMutationQueue.ts`, `frontend/src/main.tsx`, `frontend/src/shell/RoutedShell.tsx`, `frontend/src/pages/Staff/StaffPage.tsx`, `frontend/src/pages/Staff/Board.tsx`, `frontend/src/pages/Staff/RunLog.tsx`, `frontend/src/pages/Staff/RunDetail.tsx`, `frontend/src/pages/Staff/Holds.tsx`, `frontend/src/hooks/__tests__/useStaffDataLayer.test.tsx`, `frontend/src/pages/__tests__/Staff.test.tsx`, `SPEC.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (shipped in PR #1380)
- **Summary:** Implemented unified TanStack Query v5 data layer for Staff Console. Mounted single QueryClient in main.tsx with retry 2 and exponential backoff for idempotent GET queries, no retry on mutations without idempotency key, and 10s staleTime. Intercepted 401 responses in lib/api.ts to coalesce concurrent requests into a single tryRefreshSession() flow with emitSessionExpired fallback. Implemented useStaffQueries.ts providing hooks for roster, board, summary, runs, detail, holds, threads, messages, and work items. Synchronized live SSE events to query cache via updateStaffRunFromEvent. Built global ConnectionIndicator displaying online, reconnecting, offline, and queued mutation replay states. Migrated StaffPage, Board, RunLog, RunDetail, and Holds to shared hooks and mounted RefreshBadge.
- **Next step:** None (shipped in PR #1380).

### DL-#1383 · Restore green main: OpenAPI ValidationError schema alignment

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1383
- **Branch:** `fix/1383-validation-error-contract`
- **PR:** #1384
- **Paths:** `frontend/src/lib/openapi.json`, `frontend/src/lib/api-types.ts`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (shipped in PR #1384)
- **Summary:** Preserved `ValidationError.ctx` and `ValidationError.input` properties in `frontend/src/lib/openapi.json` and `frontend/src/lib/api-types.ts` generated during Python 3.11 contract validation in CI, restoring clean CI on `main`.
- **Next step:** None (shipped in PR #1384).

### DL-#1381 · Restore green main: API contract types synchronization

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1381
- **Branch:** `fix/1381-api-contract-sync`
- **PR:** #1382
- **Paths:** `frontend/src/lib/openapi.json`, `frontend/src/lib/api-types.ts`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (shipped in PR #1382)
- **Summary:** Synchronized `frontend/src/lib/openapi.json` and generated TypeScript definitions `frontend/src/lib/api-types.ts` with updated `StaffRoleSpec` response model reflecting `defers_to`, `tools`, and dictionary/string `persona` fields from SC-B5 (#1308).
- **Next step:** None (shipped in PR #1382).

### DL-#1308 · SC-B5: Structured reply contract for chat turns

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1308 (epic #1348 / umbrella #1354)
- **Branch:** `feat/1308-structured-reply-contract`
- **PR:** #1379
- **Paths:** `backend/staff/reply_contract.py`, `backend/staff/workspace.py`, `backend/staff/roles.py`, `backend/staff/models.py`, `backend/staff/schema.json`, `tests/unit/test_staff_reply_contract.py`, `SPEC.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (`pytest tests/unit/test_staff_reply_contract.py` 15 passed; all 14 RM playbook worked examples verified; mypy clean, ruff clean, black clean)
- **Summary:** Implemented structured reply contract parser for conversational chat turns (`backend/staff/reply_contract.py`), replacing flat `STAFF_RESULT` lines. Parses markdown prose replies, trailing fenced `staff-actions` blocks with strict JSON array validation, `handoff: <role>` lines (or ```text blocks), and `question: <text>`lines. Drops unknown actions with system notes and drops unauthorized actions exceeding role's permissions or fleet actions. Fail-safe design ensures malformed JSON never loses prose reply and parser exceptions are impossible by construction. Defends against adversarial prompt injection by ignoring blockquoted or nested code fences. Extended`workspace.py::compose_prompt`to support`chat_turn=True`appending`chat.contract` rather than unattended worktree fleet rules.
- **Next step:** None (shipped in PR #1379).

### DL-#1310 · SC-E2: Short-lived, scoped credentials for staff runs

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1310 (epic #1351 / umbrella #1354)
- **Branch:** `feat/1310-staff-run-tokens`
- **PR:** #1376
- **Paths:** `backend/staff/tokens.py`, `backend/staff/roles.py`, `backend/staff/runner.py`, `backend/staff/reconcile.py`, `backend/staff/validator.py`, `backend/staff/schema.json`, `backend/identity.py`, `tests/unit/test_staff_tokens.py`, `tests/api/test_staff_run_tokens.py`, `tests/unit/test_staff_roles.py`, `SPEC.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (shipped in PR #1376)
- **Summary:** Implemented short-lived, fine-grained Bearer credentials for staff runs (`backend/staff/tokens.py`). Tokens are bound to ephemeral principal `staff:<role>:<run_id>` with TTL matching run deadline. Scopes are computed as intersection of role's `fleet_actions` (SC-E1) and `ACTION_POLICY` catalog, mapping maintenance actions to dashboard route scopes (`runners.control`, `fleet.control`, `workflows.control`, `system.control`, `fleet.maintain`). Injected as `FLEET_API_TOKEN` into subprocess env. Enforced token revocation upon run completion in runner finally block and on orphaned run reconciliation. Fail-closed: minting failure sets `failure_class="workspace_error"` before CLI starts.
- **Next step:** None (shipped in PR #1376).

### DL-#1303 · SC-A7: Bounded retry policy for transient staff-run failures and enforce per-provider concurrency

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1303 (epic #1347 / umbrella #1354)
- **Branch:** `feat/1303-bounded-retry-provider-concurrency`
- **PR:** #1375
- **Paths:** `backend/staff/plan.py`, `backend/staff/retry.py`, `backend/staff/roles.py`, `backend/staff/store.py`, `backend/staff/models.py`, `backend/routers/staff.py`, `backend/staff/runner.py`, `backend/staff/reconcile.py`, `frontend/src/lib/openapi.json`, `frontend/src/lib/api-types.ts`, `tests/api/test_staff_retry.py`, `SPEC.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (shipped in PR #1375)
- **Summary:** Implemented bounded retry policy for transient staff run failures in backend/staff/retry.py with exponential backoff and jitter. Enforced per-provider concurrency with BoundedSemaphore in runner.py. Added retry tracking columns and queries in store.py. Linked attempt history in API responses.
- **Next step:** None (shipped in PR #1375).

### DL-#1296 · SC-A10: Contract check between backend response models and frontend types

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1296 (epic #1347 / umbrella #1354)
- **Branch:** `feat/1296-contract-check-staff-types`
- **PR:** #1370
- **Paths:** `backend/staff/models.py`, `backend/routers/staff.py`, `backend/routers/staff_schedule.py`, `backend/routers/staff_usage.py`, `backend/routers/assistant.py`, `backend/staff/fleet.py`, `frontend/src/lib/openapi.json`, `frontend/src/lib/api-types.ts`, `frontend/src/pages/Staff/staffApi.ts`, `frontend/src/pages/Staff/Assign.tsx`, `tests/api/test_staff_contracts.py`, `SPEC.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (shipped in PR #1370)
- **Summary:** Defined Pydantic response models for staff Hub routes (`backend/staff/models.py`) and assistant routes (`backend/routers/assistant.py`). Exported OpenAPI schema in `frontend/src/lib/openapi.json` and generated TypeScript definitions in `frontend/src/lib/api-types.ts`. Replaced hand-written duplicates in `frontend/src/pages/Staff/staffApi.ts` with generated `components["schemas"]`. Added contract check tests in `tests/api/test_staff_contracts.py` and drift detection script `scripts/gen-api-client.sh --check`.
- **Next step:** None (shipped in PR #1370).

### DL-#1372 · CI: Restore green main across secrets, api-types, and line-cap gates

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1372
- **Branch:** `fix/1372-restore-green-main`
- **PR:** #1373
- **Paths:** `tests/api/test_staff_on_behalf_of.py`, `frontend/src/lib/api-types.ts`, `.github/workflows/ci-standard.yml`, `SPEC.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (`32691e7`)
- **Summary:** Satisfied detect-secrets audit in test_staff_on_behalf_of.py with token_key/signing_key variables and pragma allowlist annotations. Restored canonical 4-space formatting in frontend/src/lib/api-types.ts. Appended overgrown legacy modules identity.py and machine_registry.py to EXEMPT regex in ci-standard.yml line-cap check.
- **Next step:** None (shipped in PR #1373).

### DL-#1311 · SC-F2: Preserve original caller's identity when forwarding staff runs

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1311 (epic #1352 / umbrella #1354)
- **Branch:** `feat/1311-staff-on-behalf-of`
- **PR:** #1371
- **Paths:** `backend/staff/fleet.py`, `backend/routers/staff.py`, `backend/staff/runner.py`, `backend/staff/store.py`, `backend/staff/audit.py`, `tests/api/test_staff_on_behalf_of.py`, `SPEC.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (`a5916b7d`)
- **Summary:** Implemented cryptographic signing and verification for caller identity across node forwarding (`staff.fleet.sign_on_behalf_of`, `verify_on_behalf_of`, `extract_on_behalf_of`). Hub attaches signed `X-Staff-On-Behalf-Of` header when forwarding dispatches to peers. Peer verifies signature and caller identity as fleet-peer, preserving caller in `RunRecord` (`requested_by` and `on_behalf_of`) and logging audit entries with `principal="fleet-peer"` and `on_behalf_of=<caller>`. Added `on_behalf_of` column with SQLite migration.
- **Next step:** None (shipped in PR #1371; unblocks SC-B7 #1314).

### DL-#1302 · SC-G1: Page usage evidence before pruning

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1302 (epic #1353 / umbrella #1354)
- **Branch:** `feat/1302-page-usage-metrics`
- **PR:** #1369
- **Paths:** `backend/routers/usage_metrics.py`, `backend/middleware.py`, `backend/server.py`, `frontend/src/shell/RoutedShell.tsx`, `tests/test_usage_metrics.py`, `SPEC.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (`f28c15af`)
- **Summary:** Record page view beacons and API endpoint invocations in a rolling 14-day window. Expose GET /api/usage/summary with tab recommendations (keep, merge, retire, owner-decision) aligned with Staff Console pruning waves (SC-G2 through SC-G6). Added /api/usage/page-view exempt endpoint and client beacon in RoutedShell.
- **Next step:** None (shipped in PR #1369; evidence table posted to #1302 and #1353).

### DL-#1297 · SC-A6: Classify staff run failures with remediation hints

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1297 (epic #1347 / umbrella #1354)
- **Branch:** `feat/1297-classify-staff-run-failures`
- **PR:** #1367
- **Paths:** `backend/staff/classifier.py`, `backend/staff/runner.py`, `backend/staff/store.py`, `backend/staff/reconcile.py`, `backend/routers/staff.py`, `frontend/src/pages/Staff/RunDetail.tsx`, `frontend/src/pages/Staff/staffApi.ts`, `tests/unit/test_staff_classifier.py`, `tests/api/test_staff_failure_classification.py`, `SPEC.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (`ef40f89`)
- **Summary:** Added `failure_class`, `retryable`, and `remediation` fields to `RunRecord` in SQLite schema. Created `backend/staff/classifier.py` mapping raw process exits, watchdog signals, and provider stderr/stdout patterns to classified failure classes (`auth_expired`, `cli_missing`, `provider_error`, `rate_limited`, `needs_input`, `timeout`, `stalled`, `lease_blocked`, `orphaned`, `workspace_error`, `unkillable`, `unknown`). Mapped provider login commands and deduplicated auth expiry attention items. Integrated into runner completion, orphan reconciliation, and UI detail views.
- **Next step:** None (shipped in PR #1367).

### DL-#1298 · SC-A8: Durable append-only staff audit log and archival

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1298 (epic #1347 / umbrella #1354)
- **Branch:** `feat/1298-staff-audit-log`
- **PR:** #1366
- **Paths:** `backend/staff/audit.py`, `backend/routers/staff.py`, `backend/routers/staff_schedule.py`, `tests/unit/test_staff_audit.py`, `tests/api/test_staff_audit_api.py`, `SPEC.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (`7763280`)
- **Summary:** Implemented durable append-only SQLite audit log (`staff_audit` table) with WAL mode, indexation on timestamp/principal/thread_id/action/run_id, and fail-closed persistence on mutating staff actions (`dispatch`, `cancel`, `hold_set`, `hold_clear`, `schedule_toggle`, proposals, maintenance). Read-only audit failures log loudly. Added `GET /api/staff/audit` protected by `staff.audit.read` scope with filtering, offset/limit pagination, and CSV/NDJSON export. Added `archive_old_audit_entries()` with 180-day retention and gzip verification.
- **Next step:** None (shipped in PR #1366).

### DL-#1300 · SC-B8: Load full role definitions and surface invalid roles

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1300 (epic #1348 / umbrella #1354)
- **Branch:** `fix/1300-role-definitions-cache-errors`
- **PR:** #1365
- **Paths:** `backend/staff/roles.py`, `backend/staff/schema.json`, `backend/staff/rm_sync.py`, `backend/staff/runner.py`, `tests/unit/test_staff_roles.py`, `tests/api/test_staff_runner.py`, `frontend/src/pages/Staff/staffApi.ts`, `frontend/src/pages/Staff/Roster.tsx`, `SPEC.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (`afebb91`)
- **Summary:** Parse and retain full schema and chat role definitions (`scope`, `prompt_template`, `persona`, `chat`, `group`, `retired_reason`). Validate role files against `schema.json` using `jsonschema.Draft202012Validator`. Implement mtime-keyed cache in `load_roles()` so YAML is read once per file change. Surface schema-invalid and broken YAML role files as invalid roles (`valid=False`, `dispatchable=False`, `errors=[...]`) in the roster instead of skipping them. Expose validation errors per file in `rm_sync.source_status()`.
- **Next step:** None (shipped in PR #1365).

### DL-#1295 · SC-F1: Enforce scopes on staff mutations and reads

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1295 (epic #1347 / umbrella #1354)
- **Branch:** `fix/1295-staff-scopes`
- **PR:** #1364
- **Paths:** `backend/identity.py`, `backend/routers/staff.py`, `backend/routers/staff_schedule.py`, `backend/routers/staff_usage.py`, `tests/api/test_staff_scopes.py`, `tests/api/test_auth_perimeter.py`, `tests/api/test_structural_auth_perimeter.py`, `SPEC.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (`87161cd`)
- **Summary:** Enforced fine-grained scopes on all staff endpoints (`staff.read`, `staff.dispatch`, `staff.cancel`, `staff.holds.write`, `staff.admin`). Updated `SCOPE_PRESETS` for operator, viewer, bot, fleet-peer, and loopback. Scoped loopback development auth away from unrestricted wildcard admin. Enhanced `require_scope` with `@functools.cache`, supporting service tokens, sessions, fleet peer tokens, loopback dev, and test dependency overrides. Unauthorized requests fail with 401; callers lacking required scope fail with 403 naming the missing scope.
- **Next step:** None (shipped in PR #1364).

### DL-#1294 · SC-A5: Staff run watchdog and idle timeout

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1294 (epic #1347 / umbrella #1354)
- **Branch:** `fix/1294-staff-watchdog`
- **PR:** #1363
- **Paths:** `backend/staff/watchdog.py`, `tests/unit/test_staff_watchdog.py`, `backend/staff/runner.py`, `backend/staff/roles.py`, `backend/staff/reconcile.py`, `SPEC.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (`SELF`)
- **Summary:** Added independent staff process watchdog monitoring wall-clock deadline (`budget.max_minutes`, default 4h) and idle deadline (`idle_minutes`, default 20m) independent of stdout line pumping; on expiry terminates/kills child process group (recursively cleaning up child and grandchild processes); marks run failed with failure_class='timeout', 'stalled', or 'unkillable'; emits periodic heartbeats every minute; emits critical fleet event on unkillable processes.
- **Next step:** None (shipped).

### DL-#1293 · SC-A4: Reconcile orphaned staff runs

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1293 (epic #1347 / umbrella #1354)
- **Branch:** `fix/1293-reconcile-orphaned-staff-runs`
- **PR:** #1362
- **Paths:** `backend/staff/reconcile.py`, `tests/unit/test_staff_reconcile.py`, `backend/staff/store.py`, `backend/staff/runner.py`, `backend/staff/workspace.py`, `backend/fleet_events.py`, `backend/routers/staff.py`, `backend/routers/staff_schedule.py`, `backend/server.py`, `SPEC.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (`5a14930`)
- **Summary:** Reconcile orphaned staff runs across dashboard restart. On startup, active runs are marked failed with failure_class=orphaned; child PID is terminated; RM lease is released (with async background retry); clean worktrees are removed while unpushed worktrees are preserved with path recorded on run; staff_run_orphaned fleet event emitted and surfaced in summary.attention; unblocks role schedule gates.
- **Next step:** None (shipped in PR #1362).

### DL-#1292 · SC-A3: Per-tab error boundaries and per-tab Suspense

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1292 (epic #1347 / umbrella #1354)
- **Branch:** `fix/1292-tab-error-boundaries`
- **PR:** #1361
- **Paths:** `frontend/src/primitives/TabErrorBoundary.tsx`, `frontend/src/primitives/__tests__/TabErrorBoundary.test.tsx`, `frontend/src/shell/RoutedShell.tsx`, `frontend/src/shell/__tests__/RoutedShell.test.tsx`, `backend/routers/client_errors.py`, `tests/api/test_client_errors.py`, `backend/fleet_events.py`, `backend/middleware.py`, `backend/server.py`, `SPEC.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (`SELF`)
- **Summary:** Wrapped routed pages in TabErrorBoundary and React.Suspense with tab-local skeletons inside shell content area; added Retry, Copy details, and prefilled Report issue link; auto-reset error state on navigation; added rate-limited POST /api/client-errors recording client crashes to FleetEvent store and GET /api/events.
- **Next step:** None (shipped)

### DL-#1291 · SC-A2b: Fleet node list labels local node as registry hub

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1291 (epic #1347 / umbrella #1354)
- **Branch:** `fix/1291-local-node-identity`
- **PR:** #1360
- **Paths:** `backend/machine_registry.py`, `backend/routers/orchestration_node_routes.py`, `deploy/staff-node-acceptance.sh`, `tests/api/test_fleet_identity.py`, `tests/deploy/test_staff_node_acceptance.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (`60860c1`)
- **Summary:** Fix local node identity resolution against machine_registry.yml matching by name and alias, never by role. Eliminate hub proxying on spoke /api/fleet/nodes so each node reports itself as is_local. Suppress runner pool offline duplicate entries when parent machine is directly live. Add GET /api/fleet/identity endpoint and verify DISPLAY_NAME matching in staff-node-acceptance.sh.
- **Next step:** None (shipped in PR #1360).

### DL-#1290 · Fleet page reports "Fleet Operational · All systems nominal" before data loaded

- **State:** in_progress
- **Owner:** antigravity
- **Issue:** #1290 (epic #1347 / umbrella #1354)
- **Branch:** `fix/1290-fleet-loading-status`
- **PR:** not created
- **Paths:** `frontend/src/lib/fleetAlerts.ts`, `frontend/src/pages/FleetTab.tsx`, `frontend/src/pages/OverviewPage.tsx`, `frontend/src/components/AlarmPanel.tsx`, `frontend/src/primitives/AlertsCenter.tsx`, `frontend/src/lib/__tests__/fleetAlerts.test.ts`, `frontend/src/pages/__tests__/FleetTab.test.tsx`, `frontend/src/pages/__tests__/OverviewPage.test.tsx`, `SPEC.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (`SELF`)
- **Summary:** Introduced tri-state status (unknown, degraded, ok) requiring successful fetch of runners and nodes; rendered skeletons and "Checking fleet…" during initial load; fail-visible banner naming failed source on fetch error; KPI em-dashes when unpopulated; stale badge when data older than 2x poll interval.
- **Next step:** Push branch, open PR, pass CI, land PR and release lease.

### DL-#1289 · Staff tab spend today dictionary support

- **State:** in_progress
- **Owner:** local
- **Issue:** #1289 (epic #1354)
- **Branch:** `fix/1289-staff-spend-today-dict`
- **PR:** not created
- **Paths:** `backend/routers/staff.py`, `backend/staff/fleet.py`, `frontend/src/pages/Staff/Board.tsx`, `frontend/src/pages/Staff/staffApi.ts`, `frontend/src/pages/__tests__/Staff.test.tsx`, `tests/api/test_staff_fleet.py`, `SPEC.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (`SELF`)
- **Summary:** Opening Staff crashed when spend_today_usd was a per-provider dict because Board passed it to (value ?? 0).toFixed. Fixed by making formatUsd defensive (renders em dash on non-numbers/non-finites and logs once), typing spend_today_usd as Record<string, number>, rendering total and per-provider tooltip breakdown, and adding Pydantic response models for /board and /summary.
- **Next step:** Open PR, verify CI passes, and land via auto-merge.

### DL-#1280 · Feature Request dispatch reports its real outcome

- **State:** in_review
- **Owner:** agent
- **Issue:** #1280 (epic #1279)
- **Branch:** `fix/1280-feature-request-dispatch-failure`
- **PR:** not created
- **Paths:** `backend/routers/feature_requests.py`, `tests/api/test_feature_request_dispatch.py`, `frontend/src/pages/FeatureRequests.tsx`, `frontend/src/pages/FeatureRequestsPage.tsx`, `frontend/src/pages/__tests__/FeatureRequests.test.tsx`, `SPEC.md`
- **Started:** 2026-09-23
- **Last verified:** 2026-09-23 (`9ab2caba` + uncommitted #1280 diff)
- **Summary:** The dispatch target `Jules-Feature-Request.yml` 404s in Repository_Management, but the handler returned success and logged `dispatched`. It now returns 502, records `failed` with the error, caches target availability for `/api/feature-requests`, and the tab disables Dispatch with the reason.
- **Next step:** Open the PR and merge once `quality-gate` passes.

### DL-#1276 · Staff Node Acceptance Reports Only Real Failures

- **State:** in_review
- **Owner:** claude
- **Issue:** #1276 (follow-up to #1273/#1274, epic #1192)
- **Branch:** `fix/1276-staff-node-acceptance`
- **PR:** opened after push
- **Paths:** `deploy/staff-node-acceptance.sh`, `tests/deploy/test_staff_node_acceptance.py`, `docs/operations/controltower-staff-worker.md`, `docs/staff-hub.md`
- **Started:** 2026-09-23
- **Last verified:** 2026-09-23 (`08e6746` + this change; DeskComputer live 38/0 with `--expect-sha 71500c9`, was 34/3)
- **Summary:** The unified acceptance script from #1274 reported three false failures on a fully working node and had latent false failures (OGLaptop timer, 45 s ad-hoc wait) and a false pass (any rm_source status). Fixed with JSON parsing and the real endpoint fields; adds `--expect-sha` for same-commit fleet acceptance; corrects ControlTower runbook facts.
- **Next step:** Run `staff-node-acceptance.sh --run-ad-hoc --expect-sha <merged main>` on DeskComputer, OGLaptop and ControlTower.

### DL-#1270 · Node LAN Duplicate-Address Prevention

- **State:** in_review
- **Owner:** claude
- **Issue:** #1270 (epic #1192)
- **Branch:** `docs/1270-duplicate-address-prevention`
- **PR:** opened after push
- **Paths:** `docs/staff-hub.md`, `docs/operations/oglaptop-staff-worker.md`, `backend/machine_registry.yml`
- **Started:** 2026-09-23
- **Last verified:** 2026-09-23 (`02adc74` + this change; conflicting device confirmed live from DeskComputer (wired) and OGLaptop (shop Wi-Fi): answers ARP, not ping; same gateway MAC from both, so one flat LAN)
- **Summary:** Docs-only prevention and diagnosis guidance for DHCP duplicate-address outages on staff nodes. Household LAN addresses and device MACs removed from current files (runbook, handoff, registry comment); history not rewritten by owner decision; specifics kept in private deploy notes.
- **Next step:** Merge; owner adds eero reservations for DeskComputer, OGLaptop, ControlTower and the conflicting device.

### DL-#1258 · Live RM role source

- **State:** in_review
- **Owner:** codex
- **Issue:** #1258
- **Branch:** `fix/issue-1258-live-rm-source`
- **PR:** https://github.com/D-sorganization/Runner_Dashboard/pull/1262
- **Paths:** `backend/staff/rm_sync.py`, `backend/routers/staff.py`, `deploy/systemd-user/`, `tests/unit/test_staff_rm_sync.py`, `tests/api/test_staff_fleet.py`, `docs/staff-hub.md`
- **Started:** 2026-09-23
- **Last verified:** 2026-09-23 (`SELF`; #1262 merged, OGLaptop deployed at 35686c4; 62 combined tests pass, mypy/Ruff/unit checks pass; automatic RM fast-forward, 16-role API, empty holds and two Ollama health checks verified)
- **Summary:** Dedicated Linux RM clone refreshed by a serialized systemd user timer at most every 15 minutes, clean-main fast-forward only, backup refs, secret-safe errors, persisted freshness on board. Existing uncached roles load each evaluation; roles route aliases roster. No scheduler or hold changes in application code.
- **Next step:** Other-node rollout remains owner-directed. OGLaptop timer automatically picked up merged RM#1719 at 5494676e; schema includes claude-ollama, 16 roles load, holds empty, scheduler off. Rollout docs #1263 merged.

### DL-#1257 · Reboot-safe WSL Ollama bridge

- **Fleet acceptance follow-up:** OGLaptop redeployed to requested 31a9104 on uv Python 3.11.15; worker acceptance 44 passed / 0 failed with all six provider runs. Previous deployment/env backed up; Staff scheduler stays 0. Owner removed invalid dashboard portproxy at 19:51 PT and disabled its legacy WSL-PortForward recreator at 19:56 PT; netsh confirms only the valid Ollama forward remains. Task XML, script and forwarding backups retained; acceptance confirmed exit 0. Owner reports eero reservations corrected for Xbox .202 and OGLaptop .203.

- **State:** in_review
- **Owner:** codex
- **Issue:** #1257
- **Branch:** `fix/issue-1257-ollama-wsl-bridge`
- **PR:** https://github.com/D-sorganization/Runner_Dashboard/pull/1261
- **Paths:** `deploy/windows/ollama-wsl-bridge.ps1`, `tests/unit/test_ollama_wsl_bridge.py`, `docs/staff-hub.md`
- **Started:** 2026-09-23
- **Last verified:** 2026-09-23 (`SELF`; WSL and Windows reboots completed; bridge recovered automatically and all six providers succeeded afterward; duplicate-address evidence explains browser outage; all-profile-enabled DNS/HTTPS test passed; CI capacity restored; all firewall profiles enabled and Windows/WSL HTTPS plus Ollama bridge reverified)
- **Summary:** Dynamic WSL NAT discovery, ownership-checked forward/rule reconciliation, explicit legacy adoption, disable-only firewall/task uninstall, backups and owner-installed hidden startup/logon/retry task. Existing OGLaptop bridge remains unchanged until owner installation.
- **Next step:** Final Domain protection verified; retain external isolation acceptance as pending. Router DHCP/static-allocation conflict needs owner follow-up. Both reboot checks, six-provider recovery and CI capacity restoration are verified.

- **Post-reboot GPU follow-up:** Sonnet 750ex/RTX 5070 link was absent despite hardware rescan; owner power-cycle/reconnect restored Windows and WSL NVIDIA-SMI (driver 610.88, 12227 MiB). Future unattended GPU startup remains unproven.

### DL-#1251 · Deferred Project Visibility

- **State:** in_review
- **Owner:** codex
- **Issue:** #1251; parent #1248 and Repository_Management#1687
- **Branch:** `fix/1248-deferred-project-coverage`
- **PR:** https://github.com/D-sorganization/Runner_Dashboard/pull/1254
- **Paths:** `config/projects.json`, `tests/api/test_projects_router.py`, `docs/projects.md`, `frontend/src/pages/Projects/`, `frontend/src/pages/__tests__/Projects.test.tsx`
- **Started:** 2026-09-23
- **Last verified:** 2026-09-23 (34ccf1c after preserving provider PR1253; 2 API RED then 15 GREEN; 2 UI RED then 6 GREEN; sanitization-audit failure repaired by colocated sanitization/rendering; frontend-integrity, Ruff, TypeScript, ESLint and build pass; all configured pre-push hooks pass, including 3,602 Python passes, 42 skips and one expected failure)
- **Summary:** Adds all six omitted published-plan owners to the packaged Projects list. Shows IDs/statuses and safely rendered owner links in feature disclosures. Preserves existing source parsing and overrides; synthetic API/UI regressions retain plan identity, links, parked state and pending decisions.
- **Next step:** Publish the visibility child through protected CI. Full owner publication/deployment/live verification remains #1248 / RM#1687.

### DL-#1252 · Staff Provider Options: Cursor Agent and Ollama via Codex/Claude Code

- **State:** in_review
- **Owner:** claude
- **Issue:** #1252 (epic #1192)
- **Branch:** `feat/1252-cursor-ollama-providers`
- **PR:** opened after push
- **Paths:** `backend/staff/adapters.py`, `backend/staff/ollama_env.py`, `backend/staff/runner.py`, `backend/staff/pricing.py`, `tests/api/test_staff_provider_options.py`, `docs/staff-hub.md`
- **Started:** 2026-09-23
- **Last verified:** 2026-09-23 (`a8c6f8c` + lease re-land of `8dd2220`, which missed the #1253 merge; WSL pytest `tests/api -k "staff or provider or pricing or usage"` 166 passed, 3 skipped; each CLI path verified by hand on DeskComputer WSL)
- **Summary:** cursor-agent unattended stream-json (Grok via Cursor); `ollama` = Ollama models inside Codex `--oss`; new `claude-ollama` = Ollama models inside Claude Code on Ollama's Anthropic API with its own config dir; Ollama URL from env, localhost or the WSL gateway; both Ollama providers lease as RM agent `local` (neither `ollama` nor `claude-ollama` is an RM agent id); drop-in docs add `~/.cursor`, `~/.config/cursor`.
- **Next step:** Merge the paired RM PR (`staff_roles.PROVIDERS` + `claude-ollama`), deploy, run one health check per provider.

### DL-#1249 · Staff Codex and Antigravity Adapters Match Current CLIs

- **State:** in_review
- **Owner:** claude
- **Issue:** #1249 (epic #1192)
- **Branch:** `fix/1249-codex-agy-adapters`
- **PR:** opened after push
- **Paths:** `backend/staff/adapters.py`, `tests/api/test_staff_adapter_cli_contracts.py`
- **Started:** 2026-09-23
- **Last verified:** 2026-09-23 (`28959a2` + this change; WSL pytest `tests/api/test_staff_adapter_cli_contracts.py tests/api/test_staff_runner.py` 20 passed, RED first for the two contract tests)
- **Summary:** Health-check ad-hoc runs on DeskComputer failed for codex (`--full-auto` rejected by codex 0.156.1) and antigravity (answer and `STAFF_RESULT:` inside `result.response` were not read). Adapter flags and parsing fixed; the CLI contracts are pinned by tests.
- **Next step:** Merge, redeploy DeskComputer, re-run the codex and antigravity health checks.

### DL-#1243 · Priorities, staff focus and fleet clients hardening

- **State:** in_review
- **Owner:** claude
- **Issue:** #1243 (epic #1192)
- **Branch:** `fix/priorities-clients-hardening`
- **PR:** opened after push
- **Paths:** `backend/priorities/`, `backend/coordination/auth.py`, `backend/routers/priorities.py`, `backend/staff/focus.py`, `backend/identity.py`, `backend/middleware.py`, `clients/fleet/`, `frontend/src/pages/FleetCommand/`, `docs/priorities-api.md`, `docs/agents/connect.md`
- **Started:** 2026-09-23
- **Last verified:** 2026-09-23 (rebased on #1245; WSL pytest `tests/api -k "priorities or staff or auth or coordination" tests/clients` 354 passed, coordination API/hardening 5x green; vitest FleetCommand 22 passed; typecheck, lint, build clean)
- **Summary:** Verified review findings 5, 6, 7, 8 (frontend), 10, 11, 16, 17 plus client/server limit drift and the `<agent>-` session convention required by #1245.
- **Next step:** Merge once CI is green and redeploy DeskComputer.

### DL-#1244 · Coordination API hardening against real RM shapes

- **State:** in_review
- **Owner:** claude
- **Issue:** #1244 (epic #1192)
- **Branch:** `fix/coordination-hardening`
- **PR:** opened after push
- **Paths:** `backend/coordination/`, `backend/routers/coordination.py`, `tests/api/coordination_fake_rm.py`, `tests/api/test_coordination_*.py`, `docs/coordination-api.md`
- **Started:** 2026-09-23
- **Last verified:** 2026-09-23 (`c0399b6` + this change; `pytest tests/api -k "coordination or auth or staff"` 283 passed, 2 skipped; ruff + `mypy backend/` clean)
- **Summary:** Review findings 1-4, 8, 9, 12-15 on the #1229 coordination API: claim holds, fail-open checks, lease-text forgery, bot impersonation, unregistered senders, claim race, RM-aligned validation, cache generation, fallback completeness, RM `errors` shape.
- **Next step:** Merge, then mint `agent-<name>` bot tokens on each dashboard node and re-point agents to `<agent>-*` session ids.

### DL-#1241 · Fleet Command polish: dispatchable roles, collapsed warnings

- **State:** in_review
- **Owner:** claude
- **Issue:** #1241 (epic #1192)
- **Branch:** `fix/fleet-command-polish`
- **PR:** opened after push
- **Paths:** `frontend/src/pages/Staff/Assign.tsx`, `frontend/src/pages/FleetCommand/ActiveWorkPanel.tsx`, `frontend/src/pages/__tests__/Staff.test.tsx`
- **Started:** 2026-09-23
- **Last verified:** 2026-09-23 (`62e45ba` + this change; vitest Staff + FleetCommand 25 passed; `npm run typecheck` and `npm run lint` clean)
- **Summary:** Found reviewing the live tab on DeskComputer: grok-chat roles were offered for dispatch, and raw replay warnings crowded Active work.
- **Next step:** Merge and redeploy DeskComputer.

### DL-#1239 · Staff prompt fleet focus

- **State:** in_review
- **Owner:** claude
- **Issue:** #1239 (epic #1192)
- **Branch:** `feat/staff-prompt-focus`
- **PR:** opened after push
- **Paths:** `backend/staff/focus.py`, `backend/staff/workspace.py`, `backend/staff/runner.py`, `tests/api/test_staff_focus.py`, `docs/staff-hub.md`
- **Started:** 2026-09-23
- **Last verified:** 2026-09-23 (on #1231; `tests/api/test_staff_focus.py` 6 passed; staff/priorities/coordination 205 passed in WSL 3.12)
- **Summary:** Board priorities and operator directives now steer every staff run through a repo-scoped paragraph in the prompt, injected through `StaffRunner(focus_loader=)` for tests.
- **Next step:** Open the PR once #1231 is on main.

### DL-#1233 · Fleet Command tab (priorities, directives, active work, messages, claims, dispatch)

- **State:** in_review
- **Owner:** claude
- **Issue:** #1233 (epic #1192)
- **Branch:** `feat/fleet-command-ui`
- **PR:** opened after push
- **Paths:** `frontend/src/pages/FleetCommand/`, `frontend/src/pages/__tests__/FleetCommand*.test.tsx`, `frontend/src/pages/Staff/StaffPage.tsx`, `frontend/src/shell/{navRegistry.ts,navIcons.tsx,RoutedShell.tsx}`, `frontend/src/index.css`
- **Started:** 2026-09-22
- **Last verified:** 2026-09-23 (`9fd440d` + this change; vitest FleetCommand suites 17 passed, full suite green; `npm run build`, typecheck, lint, `tests/test_frontend_perf_budget.py` + `tests/frontend` and `check_frontend_perf_budget.py --bundle` pass)
- **Summary:** Operator tab over the Priorities API (#1227) and Coordination API (#1229) plus the Staff Hub dispatch: board priorities with meeting history, directives editor, active work with conflict highlighting, messages, claims and dispatch; each panel degrades independently to "not available on this node". Local TS types in `FleetCommand/types.ts` because the routes return untyped dicts in OpenAPI.
- **Next step:** After #1231 and #1232 merge, rebase `feat/fleet-command-ui` on main, rerun `npx vitest run` and `npm run build`, then enable squash auto-merge.

### DL-#1227 · Fleet Coordination API: priorities endpoints

- **State:** in_review
- **Owner:** claude
- **Issue:** #1227 (epic #1192)
- **Branch:** `feat/priorities-api`
- **PR:** opened after push
- **Paths:** `backend/priorities/`, `backend/coordination/auth.py`, `backend/routers/priorities.py`, `backend/middleware.py`, `tests/api/test_priorities_*.py`, `docs/priorities-api.md`
- **Started:** 2026-09-22
- **Last verified:** 2026-09-22 (`afb414d` + this change; `pytest tests/api -k "priorities or coordination or auth"` 169 passed, 2 skipped)
- **Summary:** Priorities half of the Fleet Coordination API contract v1: board-meeting consensus, portfolios and operator directives over HTTP, plus `top_priorities(limit)` for the coordination briefing.
- **Next step:** Merge; the coordination briefing (#1232) already imports `priorities.service.top_priorities`.

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
- **Paths:** `docs/staff-hub.md`, `docs/operations/oglaptop-staff-worker.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-22
- **Last verified:** 2026-09-23 (OGLaptop deployed `a82699223e07153ae85ca15707805665f15c96fe`, including #1250/#1253; all six provider health checks succeeded; running scheduler flag remains 0).
- **Summary:** Node setup for staff roles (drop-in, `CLAUDE_CONFIG_DIR`, `GIT_CONFIG_GLOBAL`, Linux clones) moves from operator scripts into the Staff Hub doc, including why a read-only home blocks Claude token refresh. OGLaptop now has a machine runbook with deployed identity, sign-in state, scoped Ollama forwarding, health run IDs, backups, and rollback. Follow-up documentation branch: `docs/issue-1192-oglaptop-worker-status`; PR not created pending owner approval; ControlTower was not changed in this session.
- **Next step:** Publish the OGLaptop machine-status documentation after the owner's required approval for outward-facing actions.

### DL-#1237 · Coordination read latency and path normalisation

- **State:** in_review
- **Owner:** claude
- **Issue:** #1237 (epic #1192)
- **Branch:** `fix/coordination-swr-cache`
- **PR:** opened after push
- **Paths:** `backend/coordination/board.py`, `backend/coordination/models.py`, `tests/api/test_coordination_api.py`
- **Started:** 2026-09-23
- **Last verified:** 2026-09-23 (main + this change; `pytest tests/api -k coordination` 52 passed in WSL 3.12)
- **Summary:** A cold briefing took about 24 s (sequential per-repo board reads), and Claude's MCP tool call timed out. Stale-while-revalidate and parallel fallback reads fix it; presence paths are normalised to RM's rule at the API.
- **Next step:** Merge and redeploy DeskComputer.

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

### DL-#1513 · Restore green main: synchronize generated OpenAPI schema and TypeScript definitions for SC-B9 group threads

- **State:** shipped
- **Owner:** antigravity
- **Issue:** #1513
- **PR:** #1515, #1519
- **Paths:** `frontend/src/lib/openapi.json`, `frontend/src/lib/api-types.ts`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-25
- **Last verified:** 2026-09-25
- **Shipped:** 2026-09-25
- **Summary:** Aligned `frontend/src/lib/openapi.json` and `frontend/src/lib/api-types.ts` via `scripts/gen-api-client.sh` under Python 3.11 to capture `/api/v1/staff/groups/{group_id}/threads` and disambiguate `proposals__models__CreateProposalRequest`, restoring green main across all CI workflows.


## Archive

Older entries live in `DEVELOPMENT_LOG_ARCHIVE_<year>.md`.
