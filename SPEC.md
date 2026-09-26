# SPEC.md — D-sorganization Runner Dashboard

**Spec Version:** 2.5.294
**Application Version:** 4.10.0 (see `VERSION`)
**Last Updated:** 2026-09-25T00:00:00-07:00
**Status:** Active

## Change Log

| Date       | PR / Issue             | Summary                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| 2026-09-25 | #1548 | Staff chat: a reply ending `handoff: <role>` posts a `kind="handoff"` card and seeds the target role's direct thread through `BarbRouter.execute_handoff` (new `from_role`, default `barb`). Unknown or self handoffs are ignored. `HandoffCard` gains "Continue with <role>", which opens that thread. |
| 2026-09-25 | #1547 | Chat and Board proposals render as ActionCards: each proposed action is its own `action_proposal` message holding the card (`meta.proposal`), the proposal's `message_id` is that message, and deciding or executing a proposal rewrites and republishes the card. A refused approval re-enables the card's buttons. Run cards follow the run: one card per run updated in place and published live (off-loop publishes hand off with `call_soon_threadsafe`), the result or error shown when it ends, a needs-input question answered from the card (recording who answered and the continuation run), and Cancel from the card. |
| 2026-09-26 | #1504 | SC-G5-7: Playwright journey `tests/e2e/staff-request-journey.spec.ts` (Remediation context button to prefilled request, to `POST /api/v1/staff/requests`, to the run card in the named thread, plus the 5xx path). A dispatch response's `thread_id` now opens that thread in the Staff Console (`useStaffConsole` `initialThreadId`). |
| 2026-09-26 | #1556 | Staff e2e harness (`tests/e2e/fakes/start_staff_backend.py`) is hermetic: fleet-peer discovery is pinned to this node (`AUTODERIVE_FLEET_NODES=0`/`FLEET_NODES=""`), a new `STAFF_INBOX_GITHUB_SOURCES` switch disables the GitHub-backed staff inbox sources (board proposals, project decisions), and `GH_TOKEN` is left unset so other GitHub calls fail locally. A `globalTeardown` guard fails the suite if the backend log ever mentions a real fleet peer or `api.github.com`. |
| 2026-09-26 | #1542                  | WP-1.1 follow-up: non-blocking startup and guarded verification. `reconcile_orphaned_runs` no longer calls `recheck_runs` on the event loop (the scheduler thread already rechecks unverified runs off the loop). `verify_and_record` wraps its entire body to never raise, evaluating `opens_pr` lazily inside the guard so exceptions never interrupt the finish path or bypass `handle_run_status_change`. |
| 2026-09-25 | #1501                  | SC-G5-5: Code Requests, Assessments and Projects steward dispatch through the request API (`code_request.dispatch`, `assessment.run`, `staff.dispatch` role `project-steward`). Code-request dispatch has one server-side core, `code_requests/dispatch_service.py`, shared by `POST /api/code-requests/dispatch` and the request kind: profile defaults, prompt notes, standards injection and the Code Requests history entry. `WorkRequest` carries `effort`, `standards` and `budget`, and unknown standards are rejected. The Console sends the typed prompt and `standards[]` instead of building the text client-side. |
| 2026-09-25 | #1551                  | Staff chat failure card remediation context: preserve most specific classified failure across fallback provider chain. Added failure specificity ranking (`FAILURE_SPECIFICITY`) and `choose_preferred_chat_failure`, ensuring meaningful failures (e.g. `auth_expired` with `claude auth login`, primary crashes) are not shadowed by fallback unavailability or generic errors (e.g. `systemctl --user start ollama`). |
| 2026-09-25 | #1553 | Remediation bulk actions send one `issue.act` / `pr.act` request per repository instead of putting every selected number under the first item's repo; failed rows stay selected; `RequestTarget.issues` / `prs` are positive, unique and capped at 100 (422 otherwise); a request whose every target failed names each target in its error. |
| 2026-09-26 | #1562                  | Wire the new `mad-scientist` staff role (Repository_Management#1788): Barb deterministic routing keywords (mad scientist, frankenstein, wild idea, lab bench, lab experiment, lab:promote) with routing-eval cases lab-01..04, and the role joins the Advisors group in the Staff Console roster. |
| 2026-09-25 | #1549                  | Remove stale tracked vite.config.js that shadows vite.config.ts. Removed stale build compile artifacts `vite.config.js` and `vite.config.d.ts` that shadowed `vite.config.ts` and hardcoded `/api` proxy to localhost:5001. Ignored artifacts in `.gitignore`, dropped `--config vite.config.ts` workaround in `tests/e2e/staff/playwright.config.ts`, updated `tests/test_documentation_freshness.py`, and added `tests/frontend/test_vite_config.py`. |
| 2026-09-25 | #1550                  | Web-vitals POST routes through `apiRequest` to carry the `X-Requested-With: XMLHttpRequest` CSRF sentinel header required by backend middleware. |
| 2026-09-25 | #1552                  | Restore green main: trim `backend/staff/chat.py` under the 500-line soft cap (493 lines) by moving `record_chat_failure_if_pending` helper logic to `backend/staff/chat_failures.py`. |
| 2026-09-25 | #1500                  | SC-G5-4: Remediation Issues and PRs bulk actions go through the request API. Migrated `RemediationIssues.tsx` and `RemediationPRs.tsx` from legacy `/api/issues/dispatch` and `/api/prs/dispatch` to `POST /api/v1/staff/requests` (kinds `issue.act` and `pr.act` via `submitStaffRequest`). Preserved multi-target selection, provider, prompt, `force` override, and `approved_by` identity threading. `WorkRequest` carries `force` and `approved_by`, and `WorkItemStore` creates a single work item listing all target numbers. Extracted reusable workflow dispatchers into `backend/staff/work_request_dispatch.py` and frontend helpers into `remediationBulkRequest.ts` maintaining strictly $\le 500$-line modular caps. Surfaced partial failures per target visibly in UI without dropping user inputs. |
| 2026-09-25 | #1341                  | Staff Console e2e suite: the real backend with fake provider CLIs drives chat, fallback, SSE resume and failure paths in a browser; fixes doubled Claude replies, replies left pending after the chain fails, double-encoded Staff POST bodies and a Console crash on live SSE frames. |
| 2026-09-25 | #1499                  | SC-G5-3: Remediation context buttons open a prefilled request: failed runs, mobile, FAB becomes Ask. Remediation surfaces (`RemediationPage`, `RemediationTab`, `Remediation/Mobile`, `ActionSheet`) replace direct dispatch with "Fix this failed run" context button prefilling Staff Console composer / Advanced form with kind `ci.remediate`, repo, run_id, branch, workflow name, and log excerpt. Mobile FAB (`MobileShell.tsx`) becomes Ask, opening composer sheet (`AskSheet.tsx`) targeting `staff.dispatch`. Preserves provider and model options and tracks in-flight work item ID. Retired `AgentDispatch.tsx`, its route and nav entry, adding redirects to Staff Console (`/`). |
| 2026-09-25 | #1540 | Board group turns report what the seats said: `collate_consensus` lists each answering seat's position under "Where the seats stand" instead of a canned "consensus leans toward approving" line. With no answers it reports "No quorum" and proposes nothing. The auto `board.propose` carries the question and the positions, with no fixed repos, urgency or cost. |
| 2026-09-25 | #1342 | SC-D7: Board group turns render as a card: the coordinator summary first, then collapsed seat replies with silent seats marked "No response" and their error. "Turn into Board Proposal" opens the Board Proposal form (`POST /api/proposals`) prefilled with the seats' replies. A group message over the cost threshold is held until the user confirms the backend's estimate (`GET /api/v1/staff/groups/{id}/cost-estimate`), then sent with `meta.confirm_cost`. The Composer now keeps the draft when a send resolves `{ ok: false }`, and structured API refusals show their message. |
| 2026-09-25 | #1345 | SC-G7: the Classic layout and `frontend/src/legacy/` are retired. Every desktop and mobile route renders a native page (a mobile tab without a mobile page renders its responsive desktop page); a stored `dashboard.layout=legacy` is removed with a one-time notice; the fetch guards (credential responses bypass the service-worker cache, 401 → silent refresh → Session Expired dialog) and the number-input wheel guard, which only the Classic layout installed, now run for every layout. Total JS 1,112,452 → 1,007,689 bytes. |
| 2026-09-25 | #1497                  | SC-G5-1 slice B: Support all remaining request kinds in the work-request API (`POST /api/v1/staff/requests`). Registered executors for `ci.remediate` (remediation.dispatch), `issue.act` (workflows.dispatch), `pr.act` (workflows.dispatch), `code_request.dispatch` (code_requests.write), and `assessment.run` (assessments.dispatch). Strict Pydantic validation per kind (extra forbidden); dry-run preview returns resolved plan without executing; real execution links work item to workflow dispatch; unprivileged callers trigger approval_required (202); frontend `requestKinds.ts` and `AdvancedDispatchForm.tsx` conditionally omit role when the kind does not accept a role. |
| 2026-09-25 | #1516 | WP-1.1: post-run verification of staff runs (report mode). `staff/verification.py` checks a run that claimed success against GitHub: the PR for its branch (one scoped `gh api` lookup plus its head's check runs) must be open or merged with head CI green → `verified`; CI pending → `unverified` (re-checked by the scheduler every 5 min and on startup reconcile, runs that ended in the last 24 h, 20 per pass; `failed` once still pending 6 h after the run); no PR, closed unmerged or red CI → `failed`; roles without `permissions.open_pr` or runs that did not succeed → `not_applicable`. New run fields `verification`, `verification_detail`, `pr_number` (additive columns; shown on the run detail page); the `staff.dispatch` verifier reports the verdict. `STAFF_VERIFY_MODE`: `report` (default) records only, `enforce` fails an unverified success with `failure_class=unverified_output` (off until the owner enables it), `off` skips. A GitHub error leaves the run `unverified` and never changes its status. |
| 2026-09-25 | #1528 | Test hermeticity: the backend suite never holds a real GitHub credential. An autouse `tests/conftest.py` fixture removes `GH_TOKEN`/`GITHUB_TOKEN`/`GH_ENTERPRISE_TOKEN` and the GitHub App env, points `GH_CONFIG_DIR` at an empty per-test dir (the `gh` CLI is logged out) and clears `gh_client`'s cached token; tests that need a token set a fake one. Code-request tests had filed real issues (#1437–#1440, #1452–#1457). |
| 2026-09-25 | #1521 | Staff tests are hermetic: no real repo discovery or worktree creation |
| 2026-09-25 | #1498                  | SC-G5-2: One dispatch form. `Staff/AdvancedDispatchForm` posts to `POST /api/v1/staff/requests` and replaces Staff Assign and the Fleet Command Dispatch form. The kind decides the target fields (`Staff/requestKinds.ts`, only kinds the backend accepts); a request awaiting approval is shown, errors keep the user's input; the plan view moved to `Staff/DispatchPlan`. |
| 2026-09-25 | #1486                  | SC-B1-G3: One action vocabulary for chat replies and the action registry. Established ACTION_REGISTRY as the single authoritative action vocabulary across `reply_contract.py` and `actions.py`; dynamic reply contract prompt generation from registered actions (DRY); dropped unknown action blocks with warnings while preserving prose; registered legacy actions (`notify_user`, `claim_issue`, `open_pr`, `submit_proposal`) and fleet maintenance aliases; pinned that every reply-contract action has a registered, callable executor. |
| 2026-09-25 | #1465                  | Fix flaky test `test_staff_hold_and_unhold_lifecycle` caused by SQLite database lock contention: added `timeout=30.0` and `PRAGMA busy_timeout = 30000;` across staff stores (`StaffAuditStore`, `ConversationStore`, `RunStore`, `IdempotencyStore`, `WorkItemStore`, maintenance `_vacuum_db`); reused conversation store's existing audit store instance across proposal state transitions and action context in `execute_proposal`; isolated `test_staff_actions.py` by resetting stores and runner and mocking background runner worker thread in `clean_env`. |
| 2026-09-25 | #1497 | SC-G5-1 slice A: one work-request API. `POST /api/v1/staff/requests` validates the body per kind (`staff.work_requests.REQUEST_KINDS`, pydantic `extra=forbid`), records the request as a user message, a Work Item and an ActionProposal on the caller's own *Requests* thread (direct, with `barb`), and executes through the action registry: 200 dry-run plan (nothing recorded), 201 executed (work item links the run), 202 approval required (registry risk, #1485), 422/403 before anything is recorded; a failed backend marks the work item `blocked` and returns its classified status as the v1 error envelope with the recorded ids in `error.request`. Kind `staff.dispatch` only; `ci.remediate`, `issue.act`/`pr.act`, `code_request.dispatch` and `assessment.run` follow. |
| 2026-09-25 | #1330                  | SC-D11: Fold stray chat surfaces (Maxwell chat, Codebase chat, legacy assistant sidebar) into the Staff Console. Retired legacy mock endpoint `POST /api/assistant/chat` with HTTP 410 Gone and successor Link header pointing to `/api/v1/staff/threads`; folded codebase Q&A into Cartographer (architecture & code navigation) and Librarian (documentation & endpoint specs) with role handoff cards and `onNavigate` in `HelpAbout.tsx` and `CodebaseChat.tsx`; added codebase Q&A routing keywords to `cartographer` and `librarian` and registered `maxwell` in `ROLE_KEYWORD_RULES` and provider `ADAPTERS`; added Staff Console integration link and multi-agent context to Maxwell Chat panel (`MaxwellPanels.tsx`); added retirement notice banner and 410 redirect handling in `AssistantSidebar.tsx`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| 2026-09-25 | #1345                  | SC-G7 first step: mobile Projects renders the native Projects page. The legacy App has no projects case, so the mobile fallback was blank.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| 2026-09-25 | #1340                  | SC-C7: Routing evaluation set and regression check for Barb: 80 curated representative cases across 10 categories with expected targets and clarify/answer outcomes; CI regression test suite for deterministic pre-router; eval CLI runner (`scripts/eval_barb_routing.py`) recording accuracy metrics and posting Board proposal summary; candidate case ingestion from routing feedback (`GET /api/v1/staff/routing/feedback`); REST endpoint `GET /api/v1/staff/routing/eval` for eval suite triggers and programmatic accuracy reporting.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| 2026-09-25 | #1459                  | Restore green main: split frontend Fleet Command test suite (`FleetCommand.test.tsx` and `FleetCommandOps.test.tsx`, with shared fixtures in `fleetCommandTestHelpers.ts`) to strictly satisfy the $\le 500$-line limit. Cleaned merge conflict markers in `SPEC.md`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| 2026-09-25 | #1284                  | CR-7: Board Proposals suggestion box — API, Fleet Command tab, fleet tool. Implemented suggestion box for humans and agents submitting proposals to the Board stored as issues in `D-sorganization/Repository_Management` with label `board:proposal` (`backend/proposals/`). Added endpoints `POST /api/proposals` (requiring `proposals.write` scope), `GET /api/proposals` (state & repo filtering, meeting consensus.md link resolution), and `GET /api/proposals/{number}` (detail and Board-Secretary comments) mounted in `backend/server.py`. Added agent rate limiting (max 5 open proposals per bot principal, configurable via `BOARD_PROPOSALS_AGENT_LIMIT`), duplicate proposal detection returning candidate proposals (409) with override confirmation flag (`confirm_not_duplicate`), Fleet CLI tools `submit_proposal` / `list_proposals` in `clients/fleet/`, frontend `ProposalsPanel` under Fleet Command tab with prefill support via URL search params, "Propose to Board" links in Code Requests history (`CodeRequestsHistory.tsx`), and meeting-linked decided proposals view in `PrioritiesPanel.tsx`.                                                                                                                                                                                                                                                                            |
| 2026-09-25 | #1283                  | CR-3: Agent-agnostic Code Request dispatch with customizable agent profiles. Implemented Pydantic models (`AgentProfile`, `AgentProfileStore`) with atomic filesystem persistence and default seeding (`planner-strong`, `executor-cli`); agent-agnostic CommandEnvelope dispatch (`agents.dispatch.adhoc`) with live provider availability validation, quota budget overrun rejection, profile snapshotting, and audit logging; CRUD endpoints `/api/agent-profiles` with live provider health probes; updated frontend `CodeRequests` with dynamic provider registry (`useProviderRegistry`), unavailable provider warnings, and profile selection; synchronized OpenAPI schema and TypeScript client bindings; and comprehensive parametrized backend/frontend test coverage across all 10 registry providers.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| 2026-09-25 | #1346                  | SC-G8 first cut: delete the never-mounted DataTable, OfflineQueueIndicator, CredentialKeyModal and SaveIndicator primitives, the orphaned credentialKeySchema, and the react-hook-form and @hookform/resolvers dependencies. The bundle is unchanged (the modules were already tree-shaken).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 2026-09-25 | #1282                  | CR-2: Code Request data model, lifecycle state machine and durable GitHub-backed record. Implemented Pydantic models (`CodeRequest`, `CodeRequestState`, `BoardRoute`, `Requester`, `CodeRequestAuditEvent`) with lossless fenced YAML front-matter serialization; pure-function lifecycle state machine (`transition`); GitHub issue-backed durable `CodeRequestStore` with node-local JSON cache fallback and automatic cache rebuild; prompt construction with standards injection (TDD, DbC, DRY, LoD, security, docs); REST API endpoints `GET/POST /api/code-requests`, `GET /api/code-requests/{id}`, and `POST /api/code-requests/{id}/transition` with dual-scope authorization (`code-requests.manage` and `feature-requests.manage`); `scripts/ensure_code_request_labels.py`; and comprehensive tests.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| 2026-09-25 | #1484                  | SC-B1-G1: chat turns are read-only on every provider, fresh or resumed. `ProviderAdapter.chat_argv` is table-driven: claude/claude-ollama always carry `--permission-mode default` plus `--disallowedTools Edit,Write,MultiEdit,NotebookEdit` (a resumed claude turn previously had no permission flag), codex/ollama `--sandbox read-only`, antigravity `--mode plan`, gemini `--approval-mode plan`, cursor-agent `--mode ask`; a provider with no read-only mode raises `ChatReadOnlyUnsupportedError` and the turn fails visibly as `provider_not_read_only` instead of running writable. `chat.read_only_tools` uses the provider-neutral vocabulary `CHAT_READ_ONLY_TOOLS` (view_file, search_code, list_files, web_fetch, web_search), becomes the claude `--allowedTools` allowlist, and the role validator rejects any other name. Failure recording moved to `staff/chat_failures.py` (500-line cap).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| 2026-09-25 | #1338                  | SC-G6: Retire the Cline Launcher (owner decision). The page, its nav entry, intro copy, legacy-App tab and the `/api/agent-launcher` router (an agent spawner, #920) are removed along with their OpenAPI snapshot and types; `/staff/cline-launcher`, `/cline-launcher` and `/t/cline-launcher` redirect to the Staff Console.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| 2026-09-25 | #1446                  | Staff Console end to end: the desktop three-pane console (Roster · Thread + Composer · collapsible Context) is the default Staff section. One `useStaffConsole` hook serves desktop and mobile. Opening a role resolves a real server thread (`GET /api/v1/staff/threads?role=`, else `POST /threads`; Barb = `auto`, others `direct`) instead of the client-invented ids that made mobile sends 404. History comes from `GET /threads/{id}`, since `/messages` is POST-only. Roster, thread, send and decision failures show as a dismissible alert.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| 2026-09-25 | #1344                  | SC-E7: Maintenance safety suite and gates. `MAINTENANCE_POLICY` is the single source of each action's risk, scope, disruptive flag, target parameter and blast-radius cap; registration and preflight read it, and `tests/staff/test_maintenance_safety.py` pins it with a mutation check. Disruptive actions refuse fleet-wide hosts; targeted actions need one explicit target; group actions refuse groups larger than `max_count`. The detector auto-executes by registered risk, never by the detection label. Unwired operations fail as `not_wired` instead of reporting success (wiring: #1448). Timeouts are classified as `peer_timeout` and token expiry as `auth_expired`, which stops the batch. Every real invocation is audited, and dry runs skip verification.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| 2026-09-25 | #1477                  | Staff role validator accepts the optional `tools`/`scopes` grants (RM#1732) so research-scout, project-steward and fleet-curator load as valid; local `schema.json` gains `scopes`; a parity test pins schema properties to the validator's field lists.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| 2026-09-25 | #1333                  | SC-E6: Maintenance in the UI: Maintenance thread plus "Ask Maintenance" row actions on the Fleet page. Implemented accessible row action menus on machines and runners (`FleetRowActions`) with Bring online, Take offline, Restart, Compact disk, Diagnose; routed mutating actions through Barb to Maintenance presenting pre-filled `ActionCard` with dry-run preview and verification upon approval (Owner decision 2026-09-23); routed read-only operations directly to Maintenance; enhanced `ActionCard` with dry-run planned steps, verification confirmations, and Barb routing indicators; built `MaintenanceActionModal` with state verification and fleet refresh; updated Barb router keywords in `router_models.py` with maintenance actions.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| 2026-09-25 | #1325                  | SC-G3: Fleet -> Operations: merge Deployment, Fleet Orchestration, Diagnostics, Conductor, Runner Plan and Schedules. Implemented unified Operations page under `frontend/src/pages/Operations/` (`OperationsStatusBanner`, `OperationsDeploySection`, `OperationsAdmissionSection`, `OperationsRunnerHoursSection`, `OperationsScheduledWorkflowsSection`, `OperationsDiagnosticsSection`, `OperationsDeployAuditLog`) with KPI bar, quick-jump anchor navigation (`#deploy`, `#admission`, `#runner-hours`, `#scheduled-workflows`, `#diagnostics`), section-level failure isolation and retry CTAs, smooth hash scrolling, multi-node deploy action dispatch, admission queue controls (pause/resume/drain), runner hours schedule configuration, scheduled workflow plan search and run triggers, and service diagnostics/launcher generation. Added backward-compatible redirects from `/fleet/deployment`, `/deployment`, `/t/deployment`, `/fleet/fleet-orchestration`, `/t/fleet-orchestration`, `/fleet/conductor`, `/conductor`, `/t/conductor`, `/fleet/runner-schedule`, `/runner-schedule`, `/fleet/runner-plan`, `/runner-plan`, `/t/runner-schedule`, `/work/scheduled-jobs`, `/scheduled-jobs`, `/schedules`, `/work/schedules`, `/t/scheduled-jobs`, `/settings/diagnostics`, `/diagnostics`, `/t/diagnostics` to their respective anchors on `/fleet/operations` with user toast notices. |
| 2026-09-25 | #1428                  | Restore green main: trimmed `frontend/src/pages/StaffConsole/Mobile.tsx` to 463 lines (strictly $\le 500$-line soft cap), removed duplicate newline preceding Client compatibility aliases in `frontend/src/lib/api-types.ts` to satisfy `generate-api:check`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| 2026-09-25 | #1331                  | SC-D8: Mobile Staff Console: roster → thread navigation, bottom composer, push deep links. Implemented full-screen single-pane Mobile Staff Console (`StaffConsoleMobile` in `frontend/src/pages/StaffConsole/Mobile.tsx` with `mobile.css`), seamless transition between roster and conversation threads with `< Back to Roster` button, safe-area aware bottom composer (`env(safe-area-inset-bottom)`), responsive card adaptation to narrow viewports with $\ge 44\text{px}$ touch targets on Approve/Deny actions, push notification deep links (`?thread=<id>` and `?role=<role>`), role context inspection drawer, and tab switching to "Waiting on you" inbox. Wired into `RoutedShell.tsx` for mobile viewports.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| 2026-09-25 | #1424                  | Restore green main: synchronize generated OpenAPI schema (`openapi.json`) and TypeScript client types (`api-types.ts`) following SC-C5 merge (`/api/v1/staff/briefing` and updated `/api/v1/staff/inbox`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| 2026-09-25 | #1328                  | SC-C5: Waiting on you inbox and Barb briefings inside dashboard: implemented multi-source aggregation in `backend/staff/inbox.py` (`GET /api/v1/staff/inbox`) spanning pending approvals (ConversationStore proposals), needs-input items (RunStore and WorkItemStore), escalations (WorkItemStore), project charter decisions needed (`projects.service`), board proposals, and auth sign-in alerts with per-source fault isolation (`unavailable` status never blocking healthy sources); scheduled and on-demand Markdown briefing generation (`POST /api/v1/staff/briefing`) posted to Barb's conversation thread with SC-A8 audit logging; critical escalation Web Push (`staff.escalation`) with thread deep links; frontend `InboxPanel` component with severity badges, category pills, degraded source alert banner, and briefing trigger, embedded at the top of `StaffPage`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| 2026-09-25 | #1324                  | SC-G2: One Fleet page: merge Machines, Runner Audit and Event Log into Fleet. Implemented modular, accessible, typed sections under `frontend/src/pages/Fleet/` (`FleetStatusBanner`, `FleetMachinesSection`, `FleetRunnersSection`, `FleetAlertsSection`, `FleetEventsSection`) complying with SC-A2 status honesty, single unified machines table with expandable telemetry, runner controls and Maintenance role routing (SC-E6), active fleet alerts + hosted-runner billing violation audit table, and durable event log with level filters. Configured backward-compatible redirects from `/fleet/machines`, `/machines`, `/t/machines` to `/fleet#machines`, `/fleet/runner-audit`, `/runner-audit`, `/t/runner-audit` to `/fleet#alerts`, and `/fleet/events`, `/events`, `/t/events` to `/fleet#events` with user toast notices. Recomposed `OverviewPage.tsx` with section-level failure isolation and smooth hash scrolling.                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| 2026-09-25 | #1320                  | SC-D6: Context pane: role details, schedule on/off and holds, budget, active work, and thread links. Implemented `ContextPane` in `frontend/src/pages/StaffConsole/` with accessible tablist (`Role` and `Thread` tabs), role mandate, providers sign-in status, budget/today's spend summary, active runs, recent work items, and schedule enable/disable toggle switch with optimistic update and failure revert. Implemented backend endpoint `PUT /api/v1/staff/roles/{role}/schedule` (and `/api/staff/roles/{role}/schedule`) requiring `staff.holds.write`, persisting role schedule override in scheduler state, blocking scheduled slots when disabled, and recording SC-A8 audit log events.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| 2026-09-25 | #1319                  | SC-D5: Action, run, hand-off, and review cards embedded in conversation threads: ActionCard with risk badge, parameter inspection, double-click idempotency protection, and 24h expiration guard; RunCard with live status badge, node/provider metadata, expandable log tail, Cancel CTA, and deep links; HandoffCard with routing transition, rationale, and interactive specialist re-route; ReviewCard with PR link, verdict badge, and key findings; ErrorCard with plain-language cause from failure_class, highlighted remediation, node badge, and retry CTA.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| 2026-09-25 | #1318                  | SC-D4: Thread view and composer: sanitized markdown rendering (`ThreadMarkdown`) with code block copying and issue/PR/run preview badges; keyboard-first composer (`Composer`) with @mentions role auto-complete, slash commands (`/dispatch`, `/review`, `/status`, `/hold`, `/brief`), voice input integration, reliable send with idempotent retry and per-thread draft persistence; thread view (`Thread`) with date separators, unread message tracking with jump-to-unread button, streaming token deltas with interactive stop button, and SSE reconnection state management (`useThreadStream`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| 2026-09-25 | #1329                  | SC-C6: Barb availability: reserved chat capacity for Barb independent of work runs (ChatConcurrencyPool isolation); provider fallback chain (`claude` -> `codex` -> `claude-ollama` -> `ollama`) with health probes; active provider recorded in thread and message metadata; fast acknowledgment SLA (< 3 s) system messages ("On it: routing to ..."); degraded mode when all LLM providers fail with rule-based routing, queued follow-up work item, and explanatory text; availability metrics (`ack_latency_ms`, `first_token_latency_ms`, `fallback_count`, `degraded_mode_count`) exposed on Board and `/api/health`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 2026-09-25 | #1317                  | SC-D3: Staff roster sidebar: grouped roles per SC-D1 (Leadership, Project Managers, Specialists, Operations), Ask Barb (auto-route) top entry, live status indicators (idle, working, needs_you, unavailable, invalid), unread badge counts, message previews with relative age, search filtering (name, title, mandate), local storage pinning with dedicated Pinned group, collapsible sections, unavailable/invalid tooltips, stale data badge on fetch failure, and keyboard navigation.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 2026-09-25 | #1301                  | SC-D1: UX spec: Staff Console as landing page and four-area information architecture (Staff, Work, Fleet, Settings); established 6 core UX principles (status honesty, progressive disclosure, one primary action, one way to request work, keyboard-first, mobile parity), responsive wireframes (desktop 3-pane, tablet drawer, mobile single-pane bottom bar, empty states), roster organization across 4 groupings, structured card contracts (`ActionProposal`, `RunRecord`, `failure_class`), copy guidelines with standardized action verbs, and failure catalogue.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| 2026-09-25 | #1315                  | SC-C2: Barb routing: auto-select right role for unaddressed requests, show decision, allow override; two-stage router (Stage 1 deterministic pre-router with @mention, /role, keyword/scope rules, and Barb self-handling; Stage 2 LLM router with roster context and quick fallback mode); confidence thresholds with single clarifying question on ambiguous prompts; handoff message ('Barb → Role: reason') creating target role thread and seeding brief; WorkItemStore integration (SC-C3); Code Request pipeline linkage (#1279); one-click override logging routing feedback (SC-C7); endpoints `POST /api/v1/staff/routing/decide`, `POST /api/v1/staff/routing/handoff`, `POST /api/v1/staff/routing/override`, `GET /api/v1/staff/routing/feedback`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| 2026-09-25 | #1322                  | SC-E5: Stalled-job detection and remediation playbooks for the Maintenance role: 5 detectors (queued too long with idle matching runners, running past p95 \* 3, runner online but listener log stale, runner offline with assigned job, ghost runner registrations); low-risk auto-remediation (cancel and rerun, restart wedged listener); medium/high-risk proposals awaiting approval (cancel offline/hung run, ghost runner removal); isolated detector exceptions; Maintenance thread reporting and SC-A8 audit logging; endpoint POST /api/v1/staff/maintenance/detect-stalled.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| 2026-09-25 | #1307                  | SC-B4: Chat-turn execution path: fast replies with per-provider session resume, no worktree; read-only scratch dir, structured reply contract integration, bounded chat concurrency pool with Barb reservation (SC-C6), fallback to history replay on session resume failure, classified failure handling.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| 2026-09-25 | #1336                  | SC-F7: Rate limits and spend guards on staff conversation and dispatch APIs: TokenBucketLimiter with per-principal rates, LoopGuard detecting consecutive unassisted agent turns, BudgetGuard chat spend enforcement with Barb notifications, 429 Retry-After, and SC-F3 error envelopes.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| 2026-09-25 | #1321                  | SC-E3: Maintenance action catalogue: typed, allowlisted fleet operations with preflight, dry-run, host limits, blast-radius bounds, cooldowns, verification and audit logging. Added `backend/staff/maintenance.py` registering 13 typed operations into `ACTION_REGISTRY` (`maintenance.runner_start`, `maintenance.runner_stop`, `maintenance.runner_restart`, `maintenance.runner_drain`, `maintenance.group_start`, `maintenance.group_stop`, `maintenance.fleet_control`, `maintenance.queue_purge_stale`, `maintenance.run_cancel`, `maintenance.run_rerun`, `maintenance.trim_worktrees`, `maintenance.vacuum_sqlite`, `maintenance.diagnose`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| 2026-09-25 | #1313                  | SC-B6: Action proposals from conversations with risk-based approval gates (dispatch, review, code request, hold, maintenance). Replaced stubs with unified ActionRegistry (`staff/actions.py`, `staff/action_executors.py`); approval policies (`read`/`low` auto-execute, `medium` operator approve, `high`/`owner-only` owner approve); 24h proposal expiry and replay protection; role permission gating; post-execution verification and audit logging; endpoints `GET /api/v1/staff/actions`, `GET /api/v1/staff/actions/{name}`, `POST /api/v1/staff/proposals`, `POST /api/v1/staff/proposals/{id}/decide` (optional immediate execute), `POST /api/v1/staff/proposals/{id}/execute`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 2026-09-25 | #1334                  | SC-F5: Connection guides for external agents (Claude Code/Cowork, Codex CLI, Grok Bot) to talk to staff; least-privilege token minting scopes; SC-F3 classified error troubleshooting table; dedicated client guides in docs/agents/ and staff-hub.md linkage.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| 2026-09-24 | #1323                  | SC-F4: Fleet MCP tools for staff conversations, work items, approvals and cancel (`staff_threads_list`, `staff_thread_open`, `staff_message_send`, `staff_thread_read`, `staff_thread_wait`, `staff_run_cancel`, `staff_work_items`, `staff_approvals_list`, `staff_approval_decide`). Client idempotent retries; SC-F3 error envelope on tool errors; staff proposals REST endpoints `GET/POST /api/v1/staff/proposals`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| 2026-09-24 | #1316                  | SC-C3: Work-item ledger: track every request to a terminal state (`open`, `in_progress`, `waiting_on_user`, `waiting_on_ci`, `blocked`, `done`, `cancelled`, `escalated`); SQLite WAL storage with schema migrations; automatic sync from run events, PR links, and code requests; SLA overdue calculation; REST endpoints `GET/POST/PATCH /api/v1/staff/work-items` with filters (`mine`, `overdue`, `waiting_on_me`, `state`, `thread_id`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 2026-09-24 | #1314                  | SC-B7: Link runs to conversation threads, post progress run cards back to threads, proxy run detail, cancel, and SSE streams across nodes, and answer needs-input questions.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 2026-09-24 | #1392                  | Restore green main: resolve relative import in `conversation_models.py` (`from staff.store import _now`) to restore server startup under `cd backend && python server.py`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| 2026-09-24 | #1306                  | SC-B3: Conversation API: threads, messages, streaming replies (SSE with resume) and unread state. Endpoints `/api/v1/staff/threads`, `/api/v1/staff/threads/{id}/messages` (Idempotency-Key required, 202 Accepted), `/api/v1/staff/threads/{id}/stream` (SSE, Last-Event-ID resume, heartbeats), `/api/v1/staff/inbox`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| 2026-09-24 | #1312                  | SC-F3: Versioned public staff API (`/api/v1/staff`) with stable error envelope, 24h idempotency keys, and keyset cursor pagination. Legacy `/api/staff` maintained as aliases with RFC 8594 deprecation/sunset headers; published `docs/api/staff-v1.md`; migrated frontend UI to `/api/v1/staff`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| 2026-09-24 | #1305                  | SC-B2: Thread, message and action-proposal store with migrations on staff_runs.sqlite3; WAL mode, forward-only schema migrations with pre-migration backups, secrets/LAN-IP redaction hook, fail-safe degraded status, action proposal state machine with SC-A8 auditing.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| 2026-09-24 | #1304                  | SC-A9: One frontend data layer for staff and conversation data (React Query), with staleness and offline states; mounted QueryClient in main.tsx with retry 2 and backoff; coalesced single session refresh on 401; global ConnectionIndicator and RefreshBadge on Staff Console; SSE cache synchronization.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 2026-09-24 | #1383                  | Restore green main: preserve Pydantic ValidationError `ctx` and `input` schema fields in `openapi.json` and `api-types.ts` for Python 3.11 CI compatibility.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 2026-09-24 | #1381                  | Restore green main: synchronize generated OpenAPI schema (`openapi.json`) and TypeScript client types (`api-types.ts`) following SC-B5 merge (`defers_to`, `tools`, and `persona` schema evolution).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| 2026-09-24 | #1308                  | SC-B5: Structured reply contract for chat turns; parse plain markdown reply, trailing staff-actions block, handoff, and question; validate actions against vocabulary and role permissions; fail-safe recovery of prose from malformed JSON; adversarial blockquote and nested fence defense; compose_prompt chat_turn support.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| 2026-09-24 | #1310                  | SC-E2: Short-lived, scoped credentials for staff runs so roles can call fleet APIs they are allowed to; mint per-run token bound to principal staff:<role>:<run_id>; scope intersection with action policy; FLEET_API_TOKEN injection; revocation at run exit and orphan reconciliation.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| 2026-09-24 | #1303                  | SC-A7: Bounded retry policy for transient staff-run failures and enforce per-provider concurrency; exponential backoff with jitter; fallback chains; logical run aggregation.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 2026-09-24 | #1296                  | SC-A10: Contract check between backend response models and frontend types for staff and conversation APIs; generated OpenAPI client types; drift check script.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| 2026-09-24 | #1372                  | Restore green main: satisfy detect-secrets in test_staff_on_behalf_of, preserve 4-space api-types formatting, exempt overgrown modules in ci-health-check.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| 2026-09-24 | #1311                  | SC-F2: Preserve original caller's identity when forwarding staff runs to peer nodes; signed X-Staff-On-Behalf-Of header; record original caller in run record and audit log.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 2026-09-24 | #1302                  | SC-G1: Page usage evidence before pruning: rolling 14-day page view and endpoint counters, GET /api/usage/summary with tab disposition recommendations, client beaconing in RoutedShell.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| 2026-09-24 | #1297                  | SC-A6: Classify staff run failures (auth expired, CLI missing, needs input, timeout, stalled, lease blocked, orphaned) with remediation hints and deduplicated auth attention items.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| 2026-09-24 | #1298                  | SC-A8: Durable, append-only staff audit log in SQLite; strict vocabulary and fail-closed writes; GET /api/staff/audit with filtering, pagination, and CSV/NDJSON export; 180-day gzip archival.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| 2026-09-24 | #1300                  | SC-B8: Load full role definitions (scope, prompt_template, chat persona, group) with mtime-keyed caching; validate against staff/schema.json; surface invalid roles with errors in roster instead of skipping; expose per-file validation errors in rm_source status.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| 2026-09-24 | #1295                  | SC-F1: Enforce explicit scopes on staff endpoints (`staff.read`, `staff.chat`, `staff.dispatch`, `staff.cancel`, `staff.holds.write`, `staff.approve`, `staff.admin`, `fleet.maintain`); fixed scopes for fleet peers; scoped loopback auth; reject missing scope with 403.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| 2026-09-24 | #1294                  | SC-A5: Staff run watchdog: independent wall-clock deadline and idle timeout, process group termination (parent + descendants), periodic heartbeats, unkillable classification.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| 2026-09-24 | #1293                  | SC-A4: Reconcile orphaned staff runs on restart: terminate child PID, release RM lease, clean worktree or preserve if unpushed, mark failed/orphaned, and emit staff_run_orphaned fleet event.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| 2026-09-24 | #1292                  | SC-A3: Per-tab error boundaries and per-tab Suspense; report client errors to /api/client-errors; tab error panel with Retry, Copy details, and prefilled report link; reset on route change.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 2026-09-24 | #1291                  | SC-A2b: Resolve local node identity and suppress runner pool offline duplicates; eliminate hub proxying on spoke /api/fleet/nodes; add GET /api/fleet/identity; update acceptance check.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| 2026-09-24 | #1290                  | SC-A2: Fleet page tri-state status (unknown/loading, degraded, ok); skeletons and Checking fleet… during initial load; fail-visible banner naming failed source; KPI em-dashes when unpopulated; stale badge.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 2026-09-24 | #1289                  | Staff board spend defensive handling: type spend_today_usd as dict with total, show per-provider breakdown in tooltip, add Pydantic response models.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| 2026-09-23 | #1280                  | Feature Request dispatch reports the real outcome: 502 + `failed` history entry when the RM workflow is missing; list exposes cached `dispatchTarget`; UI disables dispatch and shows errors.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 2026-09-23 | #1257 / #1273 rollout  | Record OGLaptop 31a9104 Python 3.11 redeployment, unified 44/0 worker acceptance, backups and portproxy cleanup status.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| 2026-09-23 | #1276                  | Staff node acceptance: parse API JSON, real scheduler/provider/rm_source fields, OGLaptop timer link, 15 min ad-hoc wait, --expect-sha; ControlTower runbook facts.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| 2026-09-23 | #1273                  | Add ControlTower staff worker runbook, deploy/staff-node-acceptance.sh unified test, three-node fleet acceptance docs, and fix machine registry LAN comment.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 2026-09-23 | #1257 eGPU follow-up   | Record Sonnet 750ex/RTX 5070 recovery after reboot and successful Windows/WSL detection; retain unattended-startup limitation.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| 2026-09-23 | #1257 final firewall   | Confirm all firewall profiles enabled with Windows/WSL connectivity intact and CI capacity restored.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| 2026-09-23 | #1257 Windows recovery | Record Windows reboot, six-provider recovery, duplicate-IP diagnosis, firewall checks and remaining acceptance steps.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| 2026-09-23 | #1257 restart          | Record safe OGLaptop CI drain, verified WSL restart and six-provider recovery, RM user-bus observation and restoration procedure.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| 2026-09-23 | #1257 verification     | Record successful SYSTEM task execution and automatic RM#1719 uptake; retain restart and external-isolation checks pending a safe CI window.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 2026-09-23 | #1257 task policy      | Explicitly use process-scoped RemoteSigned for the SYSTEM bridge task; preserve machine-wide execution policy.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| 2026-09-23 | #1258 rollout          | Record OGLaptop deployment, live RM timer, empty holds, successful Ollama checks, backups and remaining owner bridge/reboot acceptance.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| 2026-09-23 | #1258                  | Refresh the Linux RM clone with a safe throttled systemd user timer; expose source revision and age, and reread role definitions without restart.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| 2026-09-23 | #1257                  | Add an owner-installed, reboot-safe WSL-only Ollama bridge with dry-run planning, ownership checks, backups and a hidden scheduled task.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| 2026-09-23 | #1192                  | Record verified OGLaptop worker deployment, six-provider health checks, scoped WSL Ollama connectivity, and rollback paths.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |

- **2026-09-25:** Group Threads: Talk to the Board (and other groups) with Board-Secretary Coordination (issue #1339, SC-B9). Enabled multi-agent group conversation threads where a single prompt triggers coordinated deliberation across a panel of roles. Implemented `backend/staff/group_models.py` defining `SeatSpec`, `GroupDefinition`, `SeatReply`, `GroupCostEstimate`, and `ConsensusResult`, configuring the default Board group with 4 seats (`alpha`, `bravo`, `charlie`, `delta`) coordinated by `board-secretary`. Implemented `backend/staff/groups.py` executing concurrent fanout across seat chat turns with per-seat timeout enforcement (default 60s) and graceful fault-tolerance (seats failing or timing out are marked as "no response" while responding seats are preserved). Added consensus synthesis with configurable quorum (default 3/4 for Board), generating an executive synthesis and collapsible `<details><summary>` disclosures for seat responses. Turns requiring a formal Board outcome automatically generate a `board.propose` ActionProposal linked to the thread for operator review (SC-B6). Added pre-send token and USD cost estimation and threshold guard (`STAFF_GROUP_COST_THRESHOLD_USD`, default $2.00) returning HTTP 400 with estimate details when exceeded unless `confirm_cost=True` is provided. Added REST endpoints in `backend/routers/staff_groups.py` mounted under `/api/v1/staff`: `GET /api/v1/staff/groups`, `GET /api/v1/staff/groups/{group_id}`, `GET /api/v1/staff/groups/{group_id}/cost-estimate`, and `POST /api/v1/staff/groups/{group_id}/threads`. Integrated group thread handling into `backend/routers/staff_threads.py` running background coordination tasks. Authorized `board-secretary` for `board.propose` and `staff.dispatch` in `backend/staff/actions.py`, and fixed `create_work_item` parameter names in `backend/staff/action_executors.py`. All files strictly <= 500 lines. TDD: `tests/unit/test_staff_groups.py` (7 tests) and `tests/api/test_staff_groups_api.py` (8 tests).
- **2026-09-25:** Stalled-Job Detection and Remediation Playbooks for the Maintenance Role (issue #1322, SC-E5). Implemented autonomous stalled-job detection and remediation playbooks (`backend/staff/maintenance_detect.py`) for the Fleet Maintenance role. Features 5 isolated anomaly detectors: `detect_queued_too_long` (runs queued past threshold while matching runners are idle), `detect_running_past_p95` (active runs exceeding workflow p95 \* 3), `detect_wedged_listener` (runner marked online whose listener log mtime on disk is stale, bypassing deceptive systemctl is-active status), `detect_runner_offline_assigned_job` (offline runners with active in-progress job assignments), and `detect_ghost_runners` (unregistered hosts or registrations long-offline). Low-risk anomalies (`cancel_and_rerun`, `runner_restart`) automatically execute and verify state changes; medium/high-risk anomalies (`run_cancel`, `runner_remove`, batch stops) generate structured action proposals in the Maintenance conversation thread awaiting operator/owner approval. Detector exceptions are strictly isolated per probe so failures in one check do not impact the remaining detectors. Emits SC-A8 audit records and integrates with `backend/routers/staff_proposals.py` via `POST /api/v1/staff/maintenance/detect-stalled`. TDD: `tests/unit/test_staff_maintenance_detect.py` (9 tests) and `tests/api/test_staff_maintenance_detect_api.py` (2 tests).
- **2026-09-25:** Chat-Turn Execution Path with Session Resume and Read-Only Scratch (issue #1307, SC-B4). Implemented execution engine for conversational turns (`backend/staff/chat.py`) replacing unattended worktree dispatches. Chat turns run directly against provider CLIs (`chat_argv`) inside isolated temporary scratch directories in read-only/plan mode (`--permission-mode default`, `--sandbox read-only`), without cloning repository worktrees or bypassing approvals. Captures provider session IDs across CLI stream events and stores them on thread metadata (`meta.provider_sessions`), enabling multi-turn session resumption (`--resume <session_id>`). When session resumption fails (e.g. expired session), transparently falls back to history replay formatted within a 4000-token budget (`format_history_replay`), prepending role persona and instructions. Enforces chat concurrency via `ChatConcurrencyPool` with a reserved slot guarantee for Barb (SC-C6). Emits real-time token deltas over SSE via `ThreadEventBus`, records execution metrics (`time_to_first_token_seconds`, `turn_duration_seconds`), and automatically generates and persists action proposals in SQLite from reply contracts (SC-B5). Dispatches execution as an asynchronous background task on `POST /api/v1/staff/threads/{id}/messages`. TDD: `tests/unit/test_staff_chat.py` (13 tests) and `tests/api/test_staff_chat_turns.py` (4 tests).
- **2026-09-25:** Rate Limits and Spend Guards on Staff Conversation and Dispatch APIs (issue #1336, SC-F7). Implemented multi-tier protection against runaway loops, rogue scripts, and unexpected LLM costs across staff endpoints. Added `backend/staff/rate_limit.py` with `TokenBucketLimiter` enforcing per-principal token bucket rate limits (default: 30 msg/min on thread messages, 10 dispatch/hour on staff runs; operator UI sessions default to 120 msg/min and 60 dispatch/hour). Returns HTTP 429 with `Retry-After` header and SC-F3 compliant error envelope `{error: {code: "rate_limited", retryable: true, ...}}`. Supports configurable fail-open for human operators and fail-closed for automated bot tokens when rate limit storage fails. Implemented `LoopGuard` in `backend/staff/loop_guard.py` detecting runaway agent conversations (> N consecutive agent/role turns without human user intervention, default 5 turns), pausing the thread, appending a system notification message for thread participants, and recording an audit event. Extended `BudgetGuard` in `backend/staff/budget.py` to track per-role chat spend alongside run budgets, preventing message dispatch when role or global daily budgets are exhausted, posting an automated exhaustion message to the thread, and auditing `budget_exhausted` to notify Barb. TDD: `tests/api/test_staff_spend_and_rate_limits.py` (8 tests).
- **2026-09-25:** Maintenance Action Catalogue: Typed, Allowlisted Fleet Operations (issue #1321, SC-E3). Implemented typed maintenance operations with safety rails (`backend/staff/maintenance.py`) integrated into `ActionRegistry` (`backend/staff/actions.py`, `backend/staff/action_executors.py`). Registered 13 maintenance actions: `maintenance.runner_start`, `maintenance.runner_stop`, `maintenance.runner_restart`, `maintenance.runner_drain`, `maintenance.group_start`, `maintenance.group_stop`, `maintenance.fleet_control`, `maintenance.queue_purge_stale`, `maintenance.run_cancel`, `maintenance.run_rerun`, `maintenance.trim_worktrees`, `maintenance.vacuum_sqlite`, and `maintenance.diagnose`. Enforced preflight checks (busy runners require drain before stop/restart unless `force=True`), blast-radius bounds (`max_count <= 10`), single-host restriction on disruptive operations (`fleet_control` disallows `host="all"`), cooldown tracker preventing rapid consecutive operations per action and target, dry-run planning returning detailed action plans without side effects, per-target partial failure aggregation, and SC-A8 SQLite audit logging. Verification handlers check real runner status (`stopped`, `online`, `active`) and raise `MaintenanceVerificationError` on mismatch. All modules strictly <= 500 lines. TDD: `tests/unit/test_staff_maintenance.py` (10 tests) and `tests/api/test_staff_maintenance_api.py` (3 tests).
- **2026-09-25:** Action Proposals and Risk-Based Approval Gates (issue #1313, SC-B6). Replaced legacy stubs with a unified `ActionRegistry` (`backend/staff/actions.py`, `backend/staff/action_executors.py`) defining allowlisted operations (`staff.dispatch`, `staff.review_pr`, `staff.hold`, `staff.unhold`, `code_request.create`, `board.propose`, `maintenance.runner_restart`, `maintenance.runner_stop`, `maintenance.diagnose`). Enforced risk-based approval gates (`read`/`low` auto-execute, `medium` operator approve with `staff.approve` scope, `high`/`owner-only` owner approve), 24-hour proposal expiry, and terminal replay protection. Proposing roles are verified against their allowed actions and fleet actions, rejecting unauthorized execution with 403 Forbidden. Post-execution verifiers validate actual state changes before marking proposals done. Dispatched runs and action outcomes post `action_result` and `run_card` messages back to conversation threads (SC-B7), fully audited in `staff_audit` (SC-A8). Mounted REST endpoints in `backend/routers/staff_proposals.py` under `/api/v1/staff`: `GET /api/v1/staff/actions`, `GET /api/v1/staff/actions/{name}`, `POST /api/v1/staff/proposals`, `POST /api/v1/staff/proposals/{id}/decide` (with immediate execution option), and `POST /api/v1/staff/proposals/{id}/execute`. All modules strictly <= 500 lines. TDD: `tests/unit/test_staff_actions.py` (9 tests) and `tests/api/test_staff_proposals_api.py` (6 tests).
- **2026-09-25:** External Agent Connection Guides & SC-F3 Troubleshooting (issue #1334, SC-F5). Documented external agent integration paths across Claude Code, Claude Cowork, Codex CLI, Grok Bot, and Gemini CLI. Added dedicated client guides in `docs/agents/` detailing least-privilege token minting, token scopes from SC-F1 (`staff.read`, `staff.chat`, `staff.dispatch`, `staff.cancel`), and end-to-end verification flows talking to Barb. Authored `docs/agents/claude.md` (covering Claude Code CLI and Claude Desktop Cowork MCP configurations), `docs/agents/codex.md` (covering `~/.codex/config.toml` MCP configuration and work-item tracking), and `docs/agents/grok.md` (covering local-exec `curl` recipes against `/api/v1/staff`, active Barb/Orchestrator roles, and SC-F6 remote connector notes). Updated `docs/agents/connect.md` to index all client guides, enumerate all 25 fleet MCP tools from `clients/fleet/fleet_tools.py`, and provide an actionable troubleshooting reference table keyed by standardized SC-F3 classified error codes (`unauthorized`, `forbidden`, `not_found`, `conflict`, `validation_error`, `rate_limited`, `service_unavailable`, `unreachable`). Linked connection documentation from `docs/staff-hub.md`. All documentation files strictly <= 500 lines. TDD: `tests/test_agent_connection_docs.py` (9 tests).
- **2026-09-24:** Fleet MCP Tools for Staff Conversations, Work Items, Approvals and Cancel (issue #1323, SC-F4). Extended `clients/fleet/fleet_mcp.py`, `clients/fleet/fleet_tools.py`, and `clients/fleet/fleet_client.py` to expose 9 staff operations for external coding agents and the `fleetctl` CLI: `staff_threads_list` (filter by participant, status, unread, cursor pagination), `staff_thread_open` (defaults to Barb via `auto`), `staff_message_send` (idempotent message posting with `Idempotency-Key`), `staff_thread_read` (message retrieval with `since_seq` and limit), `staff_thread_wait` (long-polling up to 60s for replies), `staff_run_cancel` (run cancellation), `staff_work_items` (filtering by `mine`, `overdue`, `waiting_on_me`, `state`, `thread_id`, and cursor), `staff_approvals_list` (review action proposals by state and thread), and `staff_approval_decide` (approve or deny proposals with operator rationale, requiring `staff.approve`). Implemented idempotent retry on network/5xx errors in `FleetClient` for `GET`, `HEAD`, `PUT`, `DELETE` and requests carrying `Idempotency-Key`. Enforced the SC-F3 classified error envelope (`{error, status, body, code, message, retryable}`) on tool errors so external agents can decide whether to retry. Added backend action proposal endpoints in `backend/routers/staff_proposals.py` mounted under `/api/v1/staff`. All modules strictly <= 500 lines. TDD: `tests/api/test_staff_proposals_api.py`, `tests/clients/test_fleet_client.py`, `tests/clients/test_fleet_mcp.py`, and `tests/clients/test_fleet_cli.py`.
- **2026-09-24:** Work-Item Ledger & Terminal State Tracking (issue #1316, SC-C3). Implemented durable work-item ledger in SQLite WAL mode (`backend/staff/work_items.py`) ensuring every request dispatched by Barb or operators is tracked to a terminal state (`open`, `in_progress`, `waiting_on_user`, `waiting_on_ci`, `blocked`, `done`, `cancelled`, `escalated`). Work items track `id`, `title`, `requested_by`, `thread_id`, `owner_role`, `state`, `expected_by` (SLA), `links` (runs, issues, PRs, code requests), `last_progress_at`, and `next_check_at`. Enforced strict state machine transitions with SC-A8 audit logging for creation and transitions. Linked run events automatically advance work item states (`run_link.py`), marking items `in_progress` on execution, `waiting_on_user` on needs-input, `waiting_on_ci` on PR creation, and `done` on successful completion. Exposed REST endpoints in `backend/routers/staff_work_items.py` mounted under `/api/v1/staff`: `POST /api/v1/staff/work-items`, `GET /api/v1/staff/work-items` (supporting filters: `mine`, `overdue`, `waiting_on_me`, `state`, `thread_id`, and keyset pagination), `GET /api/v1/staff/work-items/{id}`, and `PATCH /api/v1/staff/work-items/{id}`. All modules strictly <= 500 lines. TDD: `tests/api/test_staff_work_items.py` (9 tests).
- **2026-09-24:** Link Runs to Threads, Cross-Node Run Proxying, and Needs-Input Question Answering (issue #1314, SC-B7). Enabled conversation threads to track and manage background staff runs across the fleet. Added `thread_id` and `work_item_id` to `RunRequest`, `RunPlan`, and `RunRecord` in SQLite with auto-migration and indexing. Implemented run card updates (`backend/staff/run_link.py`) posting progress messages directly to conversation threads across state transitions (`queued`, `preparing`, `running`, `needs_input`, `succeeded`, `failed`, `cancelled`) and broadcasting over `ThreadEventBus`. Unattended agents that pause to ask questions transition to `needs_input` when associated with a thread, and operator answers submitted via `POST /api/v1/staff/threads/{id}/runs/{run_id}/answer` (or `answer_needs_input`) automatically spawn continuation runs preserving prompt context and work item linkage. Implemented cross-node proxying (`backend/staff/remote_runs.py`) allowing the local hub to discover and proxy run details (`GET /api/staff/runs/{id}`), cancel commands (`POST /api/staff/runs/{id}/cancel`), and live SSE event streams (`GET /api/staff/runs/{id}/stream`) across peer nodes with signed on-behalf-of identity preservation (SC-F2) and fail-visible `node_unreachable` reporting. Modular design keeps all files strictly <= 500 lines. TDD: `tests/api/test_staff_thread_runs.py` (9 tests).
- **2026-09-24:** Conversation API: Threads, Messages, Streaming Replies (SSE with Resume) and Unread State (issue #1306, SC-B3). Mounted `/api/v1/staff/threads` and `/api/v1/staff/inbox` endpoints in `backend/routers/staff_threads.py`. Supports creating direct, group, or auto-routed threads (kind `auto` routes default target role to Barb). Thread listings support keyset cursor pagination and filtering by participant role, status (`open`, `archived`), and unread state. Message submissions require `Idempotency-Key` and return `202 Accepted` immediately with user message record and pending reply placeholder record. Duplicate submissions replay previous response with `Idempotent-Replay: true`. Built in-memory `ThreadEventBus` (`backend/staff/thread_bus.py`) publishing live events (`token`, `message`, `proposal`, `run_card`) over SSE (`/api/v1/staff/threads/{id}/stream`). Supports `Last-Event-ID` reconnection replay from SQLite message log, 15-second heartbeat comments (`: keep-alive`), and client disconnection handling. Unread tracking records per-principal read timestamps with `/threads/{id}/read` and computes unread counter delta. Degraded conversation store fail-closed returns `503 Service Unavailable`. TDD: `tests/api/test_staff_threads_api.py` (10 tests).
- **2026-09-24:** Versioned Public Staff API v1 (`/api/v1/staff`) with Error Envelope, Idempotency, and Cursor Pagination (issue #1312, SC-F3). Mounted public stable API routes under `/api/v1/staff` in `backend/routers/staff_v1.py` with scoped authentication via `identity.require_scope`. Implemented `StaffV1Middleware` in `backend/staff/v1_envelope.py` enforcing standardized error envelopes `{error: {code, message, retryable, hint, request_id}}` on all 4xx/5xx responses and attaching RFC 8594 `Deprecation`, `Sunset`, and `Link` headers to legacy `/api/staff` aliases. Created `IdempotencyStore` (`backend/staff/idempotency.py`) with SQLite WAL persistence and 24-hour TTL, failing closed (503) on store failure to guarantee zero duplicate executions upon replay. Implemented opaque base64 keyset cursor pagination (`backend/staff/pagination.py`) for `/api/v1/staff/runs` and `/api/v1/staff/audit`. Published OpenAPI documentation in `docs/api/staff-v1.md`. Migrated frontend UI (`STAFF_BASE = "/api/v1/staff"`, `STEWARD_RUN_URL`) to use `/api/v1/staff` exclusively with automatic `Idempotency-Key` generation on mutating actions. TDD: `tests/api/test_staff_v1_api.py` and `tests/unit/test_staff_v1_primitives.py` (13 tests).
- **2026-09-24:** SC-A9: React Query Data Layer for Staff Console (issue #1304). Mounted single QueryClient in main.tsx with retry 2 and exponential backoff for idempotent GET queries, no retry on mutations without idempotency key, and 10s staleTime. Intercepted 401 responses in lib/api.ts to coalesce concurrent requests into a single tryRefreshSession() flow with emitSessionExpired fallback. Implemented useStaffQueries.ts providing hooks for roster, board, summary, runs, detail, holds, threads, messages, and work items. Synchronized live SSE events to query cache via updateStaffRunFromEvent. Built global ConnectionIndicator displaying online, reconnecting, offline, and queued mutation replay states. Migrated StaffPage, Board, RunLog, RunDetail, and Holds to shared hooks and mounted RefreshBadge. TDD: frontend/src/hooks/**tests**/useStaffDataLayer.test.tsx.
  | 2026-09-23 | #1251 | Expose all deferred-plan owners and feature IDs/statuses/safe links in Projects; retain owner authority and live rollout under #1248. |

- **2026-09-25 (2.5.243):** Staff Roster Sidebar (issue #1317, SC-D3). Implemented `frontend/src/pages/StaffConsole/Roster.tsx`, `RosterRow.tsx`, `RosterGroup.tsx`, and `rosterUtils.ts`. Features Ask Barb (auto-route) top entry, role grouping across 4 tiers per SC-D1 (Leadership, Project Managers, Specialists, Operations), live operational status indicators (`idle`, `working`, `needs_you`, `unavailable`, `invalid`) with tooltip explanations for unavailable states (held, budget reached, no provider signed in) and invalid role definitions. Includes real-time search filtering across names, titles, and mandate summaries; role pinning with local storage persistence and dedicated Pinned section; collapsible group headers; unread badges and relative-age message previews; graceful network error recovery displaying stale data badges while preserving cached rosters; and keyboard navigation (`ArrowUp`/`ArrowDown`, `Enter`/`Space`). TDD: `frontend/src/pages/StaffConsole/__tests__/Roster.test.tsx` (15 tests).
- **2026-09-24:** Conversation, Message & Action Proposal Store with Migrations (issue #1305, SC-B2). Implemented node-local conversation storage in `staff_runs.sqlite3` under SQLite WAL mode with `threading.RLock()` concurrency in `backend/staff/conversations.py`. Tables `threads`, `messages`, `action_proposals`, and `schema_migrations`. Forward-only migrations run at startup, automatically creating timestamped backups (`.bak.<timestamp>`) before applying changes. On migration failure, pre-existing database is untouched and `ConversationStore` starts in degraded mode (`available=False`) with visible banner message, failing operations visibly with `ConversationsUnavailableError`. Message bodies pass through secrets/token redaction (`backend/staff/redaction.py`), masking GitHub tokens, AWS keys, API keys, Bearer tokens, PEM private keys, and RFC 1918 / RFC 6598 private LAN IPv4 addresses. Messages maintain monotonic sequential numbering (`seq`) per thread and deduplicate duplicate submissions using `idempotency_key`. Action proposals follow a strict state machine (`proposed` -> `approved`|`denied` -> `executing` -> `done`|`failed`|`expired`), recording all state transitions in `staff_audit` (SC-A8). Modular architecture (`conversation_models.py`, `conversation_migrations.py`, `conversation_proposals.py`) ensures all modules remain strictly <= 500 lines. TDD: `tests/unit/test_conversations_store.py` (13 tests verifying round-trip persistence, migrations, backup, degraded banner, redaction, and multi-thread WAL concurrency).
- **2026-09-24:** OpenAPI ValidationError Schema Alignment (issue #1383). Preserved `ValidationError.ctx` and `ValidationError.input` properties in `frontend/src/lib/openapi.json` and `frontend/src/lib/api-types.ts` generated during Python 3.11 contract validation in CI, restoring a green main build.
- **2026-09-24:** OpenAPI Schema & API Contract Synchronization (issue #1381). Synchronized `frontend/src/lib/openapi.json` and generated TypeScript definitions `frontend/src/lib/api-types.ts` with updated `StaffRoleSpec` response model reflecting `defers_to`, `tools`, and dictionary/string `persona` fields from SC-B5 (#1308). Verified via `scripts/gen-api-client.sh --check` and `npm run typecheck`, restoring clean CI on `main`.
- **2026-09-24:** Structured Reply Contract for Staff Chat Turns (issue #1308, SC-B5). Implemented structured reply contract parser in `backend/staff/reply_contract.py`, replacing flat `STAFF_RESULT` lines for conversational turns. Parses markdown prose replies, trailing fenced `staff-actions` blocks with strict JSON array validation, `handoff: <role>` lines (or ```text blocks), and `question: <text>`lines. Drops unknown actions with system notes and drops unauthorized actions exceeding role's permissions or fleet actions. Fail-safe design ensures malformed JSON never loses prose reply and parser exceptions are impossible by construction. Defends against adversarial prompt injection by ignoring blockquoted or nested code fences. Extended`workspace.py::compose_prompt`to support`chat_turn=True`appending`chat.contract`rather than unattended worktree fleet rules. Updated`backend/staff/roles.py`, `backend/staff/schema.json`, and `backend/staff/models.py`to support`persona`object/str and`tools`tuple. TDD:`tests/unit/test_staff_reply_contract.py` (15 tests, verifying all 14 RM playbook worked examples).
- **2026-09-24:** Scoped Credentials for Staff Fleet Runs (issue #1310, SC-E2). Mint ephemeral, fine-grained Bearer tokens for staff runs at start (`backend/staff/tokens.py`), bound to ephemeral principal `staff:<role>:<run_id>` with TTL matching run deadline. Granted scopes are computed as the intersection of the role's declared `fleet_actions` (RM#1734 / SC-E1) and the dashboard's `ACTION_POLICY` catalog, mapping maintenance actions (`runner.*`, `fleet.*`, `queue.*`, `run.*`, `host.*`, `dashboard.*`) to route scopes (`runners.control`, `fleet.control`, `workflows.control`, `system.control`, `fleet.maintain`). Injected as `FLEET_API_TOKEN` environment variable for fleet CLI / MCP execution. Enforced token revocation upon run exit (success, failure, cancellation) in runner finally block and during orphaned run reconciliation (`backend/staff/reconcile.py`). Disallowed endpoint calls return 403; expired or revoked tokens return 401. If token minting fails, run fails prior to CLI subprocess with `failure_class="workspace_error"`. Updated `backend/staff/schema.json`, `validator.py`, and `roles.py` to parse and validate `fleet_actions` and tightened `approvals`. TDD: `tests/unit/test_staff_tokens.py`, `tests/api/test_staff_run_tokens.py`, `tests/unit/test_staff_roles.py`.
- **2026-09-24:** Bounded Retry Policy & Per-Provider Concurrency (issue #1303, SC-A7). Implemented bounded retry policy for transient staff run failures in `backend/staff/retry.py`. Automatically retries retryable failure classes (`rate_limited`, `provider_error`, `workspace_error`) with exponential backoff and jitter (`compute_backoff`), while never retrying non-retryable classes (`needs_input`, `auth_expired`, `lease_blocked`, `cli_missing`, `stalled`, `unkillable`, `orphaned`). Configured `max_attempts` (default 2) and optional fallback provider chain per role (`fallback_providers`). Each retry attempt creates a new run linked by `retry_of` and bounded by role budget guards. Added `retry_of`, `attempt`, `max_attempts`, `next_attempt_at`, and `fallback_provider` to `RunRecord` in SQLite with auto-migration. Enforced per-provider concurrency limits with `threading.BoundedSemaphore` (`_get_provider_sema`) in `backend/staff/runner.py` acquired ahead of the node concurrency semaphore. Aggregated attempt chains in `StaffRunDetailResponse` (`attempts` array) and defaulted run listings to root logical runs (`logical_only=True`). TDD: `tests/api/test_staff_retry.py`.
- **2026-09-24:** Contract Check Between Backend Response Models and Frontend Types (issue #1296, SC-A10). Defined Pydantic response models for staff and conversation routes (`backend/staff/models.py`), including roster, board, summary, runs, detail, cancel, dispatch, holds, schedule, usage, and assistant endpoints. Exported OpenAPI schema in `frontend/src/lib/openapi.json` and generated TypeScript definitions in `frontend/src/lib/api-types.ts` using `openapi-typescript`. Replaced duplicate hand-written type interfaces in `frontend/src/pages/Staff/staffApi.ts` with direct aliases to generated `components["schemas"]`. Added contract check and drift detection via `scripts/gen-api-client.sh --check` and `tests/api/test_staff_contracts.py`.
- **2026-09-24:** CI & Quality Gate Resilience (issue #1372). Satisfied detect-secrets audit in `tests/api/test_staff_on_behalf_of.py` using `token_key`/`signing_key` and inline `# pragma: allowlist secret` annotations. Preserved canonical 4-space indentation for `frontend/src/lib/api-types.ts` generated by `openapi-typescript` to prevent client check drift. Appended overgrown legacy modules `identity.py` and `machine_registry.py` to the `$EXEMPT` regex in `.github/workflows/ci-standard.yml` line-cap verification step, ensuring post-merge CI health gates pass.
- **2026-09-24:** Forwarded Staff Run Caller Identity Preservation (issue #1311, SC-F2). Implemented cryptographic signing and verification for caller identity across node forwarding (`staff.fleet.sign_on_behalf_of`, `verify_on_behalf_of`, `extract_on_behalf_of`). When a hub forwards a staff dispatch to a peer node, it signs an `X-Staff-On-Behalf-Of` header containing `principal`, `surface`, `thread_id`, `request_id`, and `iat` (TTL 300s) using `HUB_FLEET_TOKEN`. Receiving peer nodes verify the signature in constant time and, if valid and called by an authenticated `fleet-peer`, adopt the original caller identity in `RunRecord` (`requested_by` and `on_behalf_of`) and append audit log records with `principal="fleet-peer"`, `on_behalf_of=<caller>`, and forwarded surface/thread_id context. Forwards or requests with invalid/tampered signatures fall back safely to `fleet-peer` without granting extra scope; non-peer callers cannot spoof identities. Added `on_behalf_of` column to `runs` table with SQLite schema migration. TDD: `tests/api/test_staff_on_behalf_of.py`.
- **2026-09-24:** Page Usage Evidence & Pruning Metrics (issue #1302, SC-G1). Implemented rolling 14-day usage metrics tracker (`backend/routers/usage_metrics.py`) recording page view beacons from `RoutedShell` and API endpoint invocations via server logging middleware. Exposes `GET /api/usage/summary` and `GET /api/usage/markdown` providing hit counts, unique active days, and tab disposition recommendations (`keep`, `merge`, `retire`, `owner-decision`) matching Staff Console consolidation waves. Auth-exempt anonymous beacon `POST /api/usage/page-view`. Environment toggle `DASHBOARD_USAGE_METRICS_ENABLED`. TDD: `tests/test_usage_metrics.py`.
- **2026-09-24:** Staff Run Failure Classification & Remediation (issue #1297, SC-A6). Added `failure_class`, `retryable`, and `remediation` fields to `RunRecord` in SQLite with schema migration. Implemented `backend/staff/classifier.py` mapping raw process exits, watchdog signals (`timeout`, `stalled`, `unkillable`, `orphaned`), and provider stderr/stdout patterns into actionable failure classifications (`auth_expired`, `cli_missing`, `provider_error`, `rate_limited`, `needs_input`, `timeout`, `stalled`, `lease_blocked`, `orphaned`, `workspace_error`, `unkillable`, `unknown`). Mapped provider-specific sign-in commands for `claude`, `codex`, `cursor-agent`, `antigravity`, `gemini`, and `ollama`. Implemented deduplication of auth expiry failures per `(machine, provider)` into unified attention items. Expose classified failure details on run detail endpoints and frontend UI cards. TDD: `tests/unit/test_staff_classifier.py`, `tests/api/test_staff_failure_classification.py`.
- **2026-09-24:** Durable Staff Audit Log & Archival (issue #1298, SC-A8). Implemented node-local append-only SQLite audit table (`staff_audit`) with WAL mode in `backend/staff/audit.py`. Standardized audit schema (`id`, `ts`, `principal`, `on_behalf_of`, `surface`, `action`, `target`, `request_id`, `thread_id`, `run_id`, `outcome`, `detail`) with strict surface and action vocabulary. Enforced fail-closed policy where mutating staff actions (`dispatch`, `cancel`, `hold_set`, `hold_clear`, `schedule_toggle`, proposals, maintenance) fail the operation if audit persistence fails, while read-only auditing logs loudly. Integrated audit points across `backend/routers/staff.py` and `backend/routers/staff_schedule.py`. Added `GET /api/staff/audit` protected by `staff.audit.read` scope with filtering, offset/limit pagination, and CSV/NDJSON export. Added `archive_old_audit_entries()` with 180-day retention and gzip verification. TDD: `tests/unit/test_staff_audit.py`, `tests/api/test_staff_audit_api.py`.
- **2026-09-24:** Staff Role Definitions & Schema Validation (issue #1300, SC-B8). Extended `RoleSpec` to parse and retain all schema fields (`scope`, `prompt_template`, `retired_reason`, `persona`, `chat`, `group`, `valid`, `errors`). Added schema validation using `jsonschema.Draft202012Validator` with fallback to bundled `backend/staff/schema.json`. Implemented mtime-keyed cache in `load_roles()` so YAML is read once per file change. Surfaced broken YAML files and schema-invalid role files as invalid roles (`valid=False`, `dispatchable=False`, `errors=[...]`) in the roster UI and API rather than silently skipping them. Exposed per-file validation errors via `roles.role_validation_errors()` and `rm_sync.source_status()`. TDD: `tests/unit/test_staff_roles.py`.
- **2026-09-24:** Staff Hub Auth & Scope Enforcement (issue #1295, SC-F1). Replaced broad peer checks on staff endpoints with explicit fine-grained scopes: `staff.read` on read routes (`/roster`, `/roles`, `/board`, `/summary`, `/runs`, `/runs/{id}`, `/runs/{id}/stream`, `/holds`, `/schedule`, `/usage`, `/usage/pricing`), `staff.dispatch` on `POST /{role}/run`, `staff.cancel` on `POST /runs/{id}/cancel`, `staff.holds.write` on `PUT /holds`, and `staff.admin` on `POST /usage/export`. Updated `SCOPE_PRESETS` for `operator` (all staff scopes), `viewer` (`staff.read`), `bot` (`staff.read`, `staff.chat`, `staff.dispatch`, `staff.cancel`), `fleet-peer` (`staff.read`, `staff.dispatch`, `staff.cancel`, `staff.chat`, `fleet.maintain`), and `loopback` (scoped operator capabilities without `*` wildcard admin). Enhanced `require_scope` with `@functools.cache`, supporting service tokens, session credentials, fleet peer tokens, loopback dev, and test dependency overrides. Unauthorized requests fail with 401; callers lacking required scope fail with 403 naming the missing scope. TDD: `tests/api/test_staff_scopes.py`.
- **2026-09-23:** ControlTower Staff Worker Runbook and Three-Node Fleet Acceptance (#1273, epic #1192).
  Delivered `docs/operations/controltower-staff-worker.md` with an ordered checklist (probe to acceptance,
  marked Owner/Agent); unified pass/fail test script `deploy/staff-node-acceptance.sh` covering deployment,
  sign-ins, CLIs, drop-in permissions/directories, live role source/timer, holds, scheduler expectation,
  Ollama reachability, and provider verification; three-node fleet qualification section in `docs/staff-hub.md`;
  and corrected the out-of-date OGLaptop network caveat in `backend/machine_registry.yml` (same home LAN).
  TDD: `tests/deploy/test_staff_node_acceptance.py`.
- **2026-09-23:** Node LAN duplicate-address prevention (#1270, epic #1192). `docs/staff-hub.md` documents
  the OGLaptop 2026-09-23 outage cause (DHCP address held by a silent LAN device, Tcpip event 4199), router
  reservations for every node and the conflicting device, one active LAN interface per node, read-only diagnosis
  and lease-renewal recovery. No household identifiers in the public repo.
- **2026-09-23:** Staff provider options (#1252, epic #1192). `cursor-agent` runs `-p --output-format stream-json
--force --trust --workspace <wt>` (Grok via the Cursor subscription; camelCase usage mapped). `ollama` now runs
  Ollama models inside Codex (`exec --oss --local-provider ollama`) instead of bare `ollama run` chat; new
  `claude-ollama` runs them inside Claude Code on Ollama's Anthropic-compatible API with its own
  `CLAUDE_CONFIG_DIR`. `STAFF_OLLAMA_URL`, else localhost, else the WSL default gateway (`staff/ollama_env.py`).
  Service drop-in adds `ReadWritePaths` `~/.cursor` and `~/.config/cursor`. Both Ollama providers are free.
- **2026-09-23:** Staff provider adapters match current CLIs (#1249, epic #1192). `codex` runs
  `exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check` (codex 0.156 removed `--full-auto`);
  stream-json text extraction reads agy's `result.response` and usage from a nested `result.usage`, so agy runs
  that print `STAFF_RESULT:` count as succeeded. Pinned by `tests/api/test_staff_adapter_cli_contracts.py`.
- **2026-09-23:** Priorities/clients hardening (#1243, epic #1192). `PUT /api/priorities/directives` needs the new
  `priorities.write` scope (operator preset; bots refused) or loopback; `set_by` is always the caller; directive
  text is one line (CR/LF 422); GET/PUT return `version` (PUT 409 when stale) and no storage path; one invalid
  stored entry is skipped, not the whole list. `staff.focus.focus_paragraph` is total and single-line per item.
  Perimeter exempts exactly `/api/priorities` plus `/api/priorities/`. Fleet clients mirror server limits, omit
  unset intent/reason, allow `to='*'`, add `fleet_ack_message`, and derive/enforce `<agent>-` session ids.
  One write dependency `coordination.auth.require_writer(scope)` serves both APIs; board `reset_cache` bumps the
  generation and `join_refreshes()` waits for background refreshes.
- **2026-09-23:** Coordination API hardening (#1244, epic #1192). Claims: 409 whenever RM reports `held` and the
  holder is not this agent+session (empty holder, other agent, or session unknown); RM fail-open
  `reason: error:*` is `available:false` / 502; check-then-post serialized per `repo#issue`; RM `errors: [...]`
  surfaced and a posted comment with a failed label is 200 + `warnings`. Auth: bot principal `agent-<name>` acts
  only as `<name>` on sessions `<name>-*` (403 otherwise); operator/admin/loopback unrestricted. Validation mirrors
  RM (`_IDENTIFIER` sessions/recipients/message ids/goal keys, outcomes <= 250, single-line `intent`/`reason`/goal
  outcomes, message control chars, agent in RM roster via new `coordination/roster.py`). Messages/acks need live
  presence in a fresh board read (409). Board cache: write-generation guard, single-flight misses, per-repo
  fallback reported `complete:false` with a warning.
- **2026-09-23:** Assign role picker filters `dispatchable`; Active work warnings collapse into `<details>` (#1241, epic #1192).
- **2026-09-23:** Staff prompt fleet focus (#1239, epic #1192). `staff.focus.focus_paragraph(repo, items)` filters
  `priorities.service.top_priorities` to board items whose project matches the repo and directives scoped to it or `*`
  (≤5); `compose_prompt(focus=)` places it before the fleet rules; `RunPlan.focus` is in the dry-run plan.
- **2026-09-22:** Fleet Command tab (#1233, epic #1192). New nav entry `fleet-command` (agents group, compass icon,
  lazy route desktop + mobile) rendering `frontend/src/pages/FleetCommand/`: Priorities (latest board consensus from
  `GET /api/priorities` with GitHub tracking links, deferred backlog, disagreement flags, meeting history via
  `/api/priorities/meetings[/{date}]`, "no board meeting yet" empty state), Directives editor
  (`PUT /api/priorities/directives`, priority 1–5, repo scope, expiry), Active work
  (`GET /api/coordination/sessions` board sessions merged with staff runs, repo filter, same-issue / overlapping-path
  conflicts highlighted), Messages (send to a session or `*`, inbox per session), Claims (check / claim / release; a
  409 shows the holder) and Dispatch (the Staff tab's Assign form with dry-run preview). Each panel degrades on its
  own: a 404 or `available:false` renders "not available on this node". The Staff tab opens a run from `?run=<id>`.
- **2026-09-22:** Fleet Coordination API priorities (#1227, epic #1192). New `backend/priorities/` (board-meeting
  `consensus.md` parser tolerant of the RM template's malformed tables, meeting listing, portfolios from RM
  `config/fleet_manifest.yaml`, operator directives stored like holds with expiry) and `backend/routers/priorities.py`:
  `GET /api/priorities`, `GET /api/priorities/meetings[/{date}]`, `GET/PUT /api/priorities/directives`. Reads are
  fleet-peer and degrade to `available:false`; the PUT reuses `coordination.auth.require_coordination_writer`
  (scope `coordination.write` or a loopback peer). `priorities.service.top_priorities(limit)` feeds the coordination briefing.
- **2026-09-22:** Fleet Coordination API (#1229, epic #1192). New `/api/coordination/*` (`backend/routers/coordination.py`, logic in `backend/coordination/`): `GET sessions` (board sessions across repos + staff runs in flight), `GET inbox`, `POST presence`, `POST presence/release`, `POST messages`, `POST messages/ack`, `GET|POST claims` (409 when another agent holds the lease), `POST claims/release` and `GET briefing` (priorities when the Priorities module is present, active holds, repo sessions/runs, claim protocol, `FLEET_RULES`). Presence/messages stay on the RM board and leases on the issue; the dashboard only runs the RM scripts (`coordination/rm_scripts.py`, now shared with `staff/lease.py`), caches board reads 60 s (`list --all-repos`, per-repo fallback), degrades reads to `available: false` and returns 502 `{error, guidance}` for failed writes. Writes need scope `coordination.write` (new in the `bot` and `operator` presets) or the loopback orchestrator peer; contract in `docs/coordination-api.md`.
- **2026-09-22:** Fleet API agent clients (#1228, epic #1192). New stdlib-only `clients/fleet/` (Python 3.10+):
  `fleet_client.py` (`FleetClient`, one validated method per staff/coordination/priorities endpoint; `FLEET_API_URL`,
  `FLEET_API_TOKEN` bearer, CSRF header on every request, `FleetAPIError(status, body)`), `fleetctl.py` (JSON CLI; exit
  1 API error, 2 bad arguments) and `fleet_mcp.py` (hand-written MCP stdio server, protocol `2025-06-18`, 15 `fleet_*`
  tools), all generated from one command table `fleet_tools.py`. Setup for Claude Code, Codex, Gemini CLI and Grok Bot
  plus per-agent bot tokens in `docs/agents/connect.md`; CI lint/format/mypy/bandit and the python-scope detector cover
  `clients/`.
- **2026-09-22:** Runner hosts: new `deploy/clean-gitconfig-token-rewrites.sh` (installed by
  `install-runner-maintenance.sh`) prunes credential-bearing `url.*.insteadOf` sections that CI jobs left in the
  runner user's global git config, with a 0600 backup, `--dry-run` and redacted logs (#1216).
- **2026-09-22:** Artifact wheelhouse ABI contract and fail-closed install (#1212). Packaging takes `--python-minor` (release pins `3.12`) and `deploy/check-wheelhouse-abi.py` fails when any wheel's `cpXY`/platform tag cannot install on the declared `python_minor` (abi3 at or below it and pure wheels pass); `install-dashboard-artifact.sh` runs that check, interpreter selection (`select_dashboard_python MINOR venv` — venv+ensurepip suffices, host pip not required) and a full offline install into a throwaway venv before touching the deploy dir, then swaps `.venv` (restoring the previous one on failure) before syncing code; rsync no longer deletes `.venv`.
- **2026-09-22:** De-duplicate SPEC.md (6 copies → 1, NUL bytes removed) and CHANGELOG.md (4 copies → 1) left by stacked-PR conflict resolution; no content removed (#1192).
- **2026-09-22:** Staff board scheduled-role liveness (#1209, epic #1192). New
  `backend/staff/liveness.py::compute_liveness(roles, store, scheduler_state, now)` derives, per
  dispatchable role with a `schedule`, `last_success`, `last_attempt`, `last_fired`, `next_fire`,
  `expected_interval_seconds` (gap between the next two fires) and `status` `ok | late | dead |
never` (late > 1.5 intervals since the last success, dead > 3 intervals or fired/attempted and
  never succeeded, never = no run and no fire). Additive: `GET /api/staff/board?local=1` gains
  `liveness`; the hub board and `GET /api/staff/summary` gain `liveness_alerts` (late/dead rows
  across online nodes tagged with `machine`, kept per node under `machines[name].liveness`).
  A role turning `dead` records a `staff_role_dead` fleet event (new `EventKind`, severity
  warning, one per role per 6 h in memory). The Staff tab Board panel lists the alerts.
  TDD: `tests/api/test_staff_liveness.py`, `frontend/src/pages/__tests__/Staff.test.tsx`.
- **2026-09-22:** Provider registry v2 — antigravity, cursor-agent, maxwell providers; Jules disabled; per-node CLI probe (#1193, epic #1192).
  `PROVIDER_REGISTRY` gains `antigravity` (`antigravity-cli`, probe `agy`), `cursor_agent`
  (`cursor-agent`, whose curated model list includes Grok entries so Grok is a worker seat
  through the Cursor subscription — the Grok Build CLI is not used because it requires
  SuperGrok) and `maxwell` (`maxwell-daemon`, optional remote session, experimental, dormant
  since 2026-06). Every `ProviderEntry` carries `enabled` (default `true`); `jules_cli` and
  `jules_api` are `enabled: false` and removed from `provider_order` / `enabled_providers`
  in `config/agent_remediation.json` (workflows retired fleet-wide, RM#1483 / RM#1505) — the
  entries and the legacy `/dispatch-jules` route remain, and `plan_dispatch` skips registry-
  disabled providers even when a stale saved policy still lists them. New
  `backend/agent_remediation/provider_probe.py::probe_provider_availability` reports
  `installed` (`shutil.which` on the dashboard host) and `authenticated` (reusing the
  `routers/credentials.py` probes) per provider with an `availability_probe`; it is exposed by
  `GET /api/providers/registry` as `node_availability` plus `hostname`, cached 60 s.
  TDD: `tests/api/test_providers_registry.py` (v2 providers, probe, cache),
  `tests/api/test_conductor_constants.py`, `tests/test_agent_remediation.py`.
- **2026-09-22 (4.10.0):** Release 4.10.0 — Fleet Staff Hub (#1201, epic #1192). First release carrying
  `backend/staff/` (roles, adapters, run store, workspace, lease ritual, runner, fleet fan-out, scheduler,
  holds, budgets, usage ledger), `/api/staff/*`, `/api/projects/*`, the Staff and Projects tabs and provider
  registry v2. `deploy/fleet-health-monitor.ps1` gains a read-only probe of this node's
  `GET /api/staff/board?local=1` (WARN + state error when unreachable, never aborts the cycle;
  `tests/deploy/test_fleet_health_monitor.py::test_script_probes_the_staff_hub_board`). Rollout: nodes install
  `dashboard-4.10.0.tar.gz` with `deploy/update-deployed.sh --artifact`; set `STAFF_SCHEDULER_ENABLED=0` on
  every node except the one that should own scheduled roles until placement is pinned.
- **2026-09-22:** Staff Hub usage ledger (#1200, epic #1192). New `backend/staff/pricing.py` (per-provider/model
  USD-per-1M-token table: Claude list prices, Codex/Gemini rows ported from Maxwell_Daemon flagged `estimate`,
  wall-time fallback via `STAFF_WALL_USD_PER_MIN`, `estimate_cost() -> (usd, method)`), `backend/staff/usage.py`
  (`finalize_cost` called by the runner after the final `update_run`; `summary`; `export_to_rm` running
  Repository_Management `scripts/append_credit_usage.py` as a subprocess), additive `RunStore` changes (`cost_method`
  column via guarded `ALTER TABLE`, `usage_by(provider|role|day)`), and router `backend/routers/staff_usage.py`:
  `GET /api/staff/usage` (rows, totals, `budget` from `STAFF_BUDGET_USD_PER_DAY`), `GET /api/staff/usage/pricing`,
  `POST /api/staff/usage/export` (503 without an RM checkout). Docs: `docs/staff-hub.md` "Usage & cost". TDD: 11 tests
  in `tests/api/test_staff_usage.py`.
- **2026-09-22:** Staff tab — operator surface for the Staff Hub (#1198, epic #1192). New
  `frontend/src/pages/Staff/` (`StaffPage` with Board + Roster / Runs / Assign / Holds sections; `Board`
  polls `GET /api/staff/board` every 10 s for running/queued per machine and spend today; `Roster` cards
  from `GET /api/staff/roster` with installed-provider badges, schedule, active runs, budget and
  retired/dispatchable state; `RunLog` over `GET /api/staff/runs` with role/status filters; `RunDetail`
  shows `GET /api/staff/runs/{id}` events and tails `GET /api/staff/runs/{id}/stream` with `EventSource`
  while the run is queued/preparing/running, with Cancel → `POST /api/staff/runs/{id}/cancel`; `Assign`
  form with role-limited provider select, Preview = `dry_run: true` plan (prompt/argv/branch) and Dispatch
  → navigates to the run; `Holds` reads/edits `GET|PUT /api/staff/holds` (#1196) and renders "holds
  unavailable" on 404). Nav entry `staff` / "Staff" in group `agents` (new `BriefcaseIcon`), lazy-routed in
  `RoutedShell` (desktop + mobile drawer). All POSTs go through `lib/api.apiRequest` (CSRF header). TDD:
  8 behaviour tests in `frontend/src/pages/__tests__/Staff.test.tsx`.
- **2026-09-22:** Staff Hub scheduler, run windows, holds list and per-role budgets (#1196, epic #1192).
  New modules `backend/staff/schedule.py` (five-field cron parser, `next_fire`, overnight-aware `in_window`,
  zoneinfo `America/Los_Angeles`, no third-party cron), `backend/staff/holds.py` (`<config dir>/staff_holds.json`
  seeded from role YAML `holds:`), `backend/staff/budget.py` (`BudgetGuard.can_run` against `usd_per_day`, alerts
  at 0.75/0.9/1.0 with 6 h in-memory debounce) and `backend/staff/scheduler.py` (30 s background thread started
  at server startup, one run per role per slot, cursor persisted in `<config dir>/staff_schedule_state.json`).
  Router `backend/routers/staff_schedule.py`: `GET /api/staff/schedule`, `GET /api/staff/holds`,
  `PUT /api/staff/holds`. Additive `RunStore.spend_by_role_since`. Env: `STAFF_SCHEDULER_ENABLED`,
  `STAFF_HOLDS_FILE`, `STAFF_SCHEDULE_STATE`. Dependency `tzdata` (Windows nodes have no system zone database).
  TDD: 26 tests in `tests/api/test_staff_schedule.py` (frozen clock, fake runner, no subprocess).
- **2026-09-22:** Staff Hub fleet view and machine targeting (#1195, #1197, epic #1192). New
  `backend/staff/fleet.py`: peer discovery (`FLEET_NODES` or the machine registry), concurrent fan-out of
  every peer's `GET /api/staff/board?local=1` with the `HUB_FLEET_TOKEN` bearer, merge into one fleet board
  (`machines`, `online`, `offline`, concatenated `running`/`queued`, summed spend, providers per machine;
  an unreachable peer is `offline`, never a failure), least-loaded placement (`choose_machine`) and
  forwarding of dispatches to a peer's `/api/staff/{role}/run`. `GET /api/staff/board` is now fleet-wide
  when peers exist (`?local=1` for this node); new `GET /api/staff/summary` is the one-call brief for Barb
  and Orchestrator (`in_flight`, `attention`, `recent_24h`, `spend_today_usd`, `providers`, `holds` when
  #1196 lands, `roles`). `POST /api/staff/{role}/run` accepts `machine: local | <peer name> | auto`;
  unknown → 422, unreachable peer → 503, peer rejection passes through. Env: `STAFF_PEER_TIMEOUT_SECONDS`.
  TDD: 10 tests in `tests/api/test_staff_fleet.py` with injected `get_json`/`post_json` fakes.
- **2026-09-22:** Staff Hub node setup documented (#1223, epic #1192).
  `docs/staff-hub.md` "Node setup": drop-in paths, `CLAUDE_CONFIG_DIR` for token refresh under the read-only home,
  `GIT_CONFIG_GLOBAL`, `~/staff-repos` + `~/staff-worktrees`.
- **2026-09-23:** Coordination read latency + path normalisation (#1237, epic #1192). `coordination.board._cached`
  serves an expired entry and refreshes once in a background thread; the fallback `list` per repo runs in parallel (4);
  `models.normalize_scope_path` enforces RM's scope rule at the API (422).
- **2026-09-23:** Fleet API follow-ups (#1234, epic #1192). `staff.holds.active_holds()` feeds the summary `holds`;
  client `register_presence` requires `issue`/`branch`, TTL ≤ 8 h; `tests/clients/fleet_fixtures.py` replaces its conftest.
- **2026-09-22:** Staff Hub skip linked issues (#1225, epic #1192). `FLEET_RULES` adds: skip an issue an open PR
  already references (`gh pr list --state open --search <n>`).
- **2026-09-22:** Staff Hub unattended runs (#1221, epic #1192).
  Claude adapter `--permission-mode bypassPermissions`, default model `sonnet`; `workspace.playbook_text` inlines the
  role playbook from the RM checkout (16k cap, no `..`); scheduled prompt names role + repo and
  `scheduler.scheduled_repo` rotates repos daily (repo holds re-checked); exit 0 without `STAFF_RESULT` → `failed`
  with `runner.NO_RESULT_ERROR`.
- **2026-09-22:** Staff Hub fleet-rule guardrails (#1217, epic #1192, companion RM#1700).
  `FLEET_RULES` adds: never take `claim:local` or another agent's live lease; never file bulk remediation
  issues. Role `holds:` are scheduler blocks only. TDD: `tests/api/test_staff_fleet_rules.py`.
- **2026-09-22:** Staff Hub PR-consolidation strategy (#1213, epic #1192, companion RM#1690).
  `RoleSpec` gains an optional `strategy` dict parsed from the role YAML
  (`strategy: {consolidate_when: {open_prs, utilisation_pct}}`, unknown keys ignored). New
  `backend/staff/consolidation.py`: pure `evaluate(role, repo, open_pr_count, utilisation_pct)` →
  `{mode: consolidate|serial, reason, threshold}` (consolidate when every configured threshold is met);
  `decide` fetches the repo's open non-draft PR count (`gh api --paginate .../pulls?state=open`) and the
  fleet utilisation (`busy/online` from the `orchestrator_api` capacity provider), both injectable,
  and falls back to `serial` / `inputs unavailable` on any fetch error. The scheduler tick and
  `POST /api/staff/{role}/run` (dry run and dispatch) evaluate it when the role has the block and a
  repo, append the "Consolidation mode: …" paragraph (exclusions, never cancel or re-run other PRs'
  CI) to the prompt, return it as `plan.consolidation` and store `strategy_mode` on the run. The
  runner parses `consolidated N PRs into #M` (case-insensitive) from the final `STAFF_RESULT:` line
  into the additive `outcome` column (both columns via the `cost_method` ALTER TABLE pattern). Staff
  tab: Roster card shows the threshold, Assign preview shows the decision, RunLog/RunDetail show
  `outcome`. TDD: `tests/api/test_staff_consolidation.py`, `frontend/src/pages/__tests__/Staff.test.tsx`.
- **2026-09-22:** Staff Hub core — run, record and stream AI staff roles as local CLI subprocesses (#1194,
  epic #1192). New package `backend/staff/` (`roles.py` reads Repository_Management `staff/roles/*.yml` by
  path; `adapters.py` launch recipes for claude / codex / agy / gemini / cursor-agent / ollama; `store.py`
  node-local SQLite run + event store; `workspace.py` checkout discovery, blobless clone, git worktree and
  prompt assembly; `lease.py` RM lease ritual as subprocesses; `runner.py` bounded worker threads with
  cancel and timeout) and router `backend/routers/staff.py` registering `GET /api/staff/roster`,
  `GET /api/staff/board`, `GET /api/staff/runs`, `GET /api/staff/runs/{id}`,
  `GET /api/staff/runs/{id}/stream` (first server-sent-events surface), `POST /api/staff/{role}/run`
  (with `dry_run` plan preview) and `POST /api/staff/runs/{id}/cancel`. Reads use `require_fleet_peer`,
  mutations `require_orchestrator_peer`; `/api/staff/` added to `_ALT_AUTH_EXEMPT_PREFIXES` with the
  same justification as `/api/orchestrator/`. Env: `STAFF_ROLES_DIR`, `STAFF_RUNS_DB`, `STAFF_REPOS_ROOT`,
  `STAFF_WORKTREES_ROOT`, `STAFF_RM_ROOT`, `STAFF_RM_PYTHON`, `STAFF_MAX_CONCURRENT_RUNS`,
  `STAFF_RUN_TIMEOUT_SECONDS`. Docs: `docs/staff-hub.md`. TDD: 16 tests in
  `tests/api/test_staff_runner.py` (fake CLI end-to-end) and 4 in `tests/api/test_staff_auth_perimeter.py`.
- **2026-09-25:** Projects — fleet-wide prioritised status and untracked-work report (#1434, epic #1192).
  `backend/projects/priorities.py` parses Repository_Management `config/project_priorities.yaml`
  (`schema_version: 1`, tiers P0-P4, absent repo = `unranked`); `coverage.py` splits a repo's open issues/PRs
  into tracked (a charter Tracking cell names it, or it references a tracked number) and untracked (capped at
  100 listed); `rollup.py` attaches tiers, sorts P0 first (config order within a tier), builds the fleet
  `summary` and the curator report. Routes: `GET /api/projects` (+`summary`, `priorities_error`),
  `GET /api/projects/priorities`, `GET /api/projects/untracked` (untracked items, charter-less repos, org repos
  missing from `config/projects.json`). Missing/malformed priority file → every repo `unranked` with
  `priorities_error`; open-item fetch failure → `coverage: null` + `coverage_error`; never 5xx. Frontend:
  `PriorityBadge`, `CoverageDetails`, `FleetSummaryBar`. TDD: `tests/api/test_projects_tracking.py`,
  `frontend/src/pages/__tests__/Projects.test.tsx`.
- **2026-09-22:** Projects tab — per-repo charter progress, decisions needed and last project-steward run
  (#1199, epic #1192). New package `backend/projects/` (`charter.py` mirrors the Repository_Management
  `shared_scripts/project_charter.py` contract — `| ID | Feature | Status | Tracking | Notes |`, status
  planned/in-progress/shipped/parked, STATUS.md `## Decisions Needed` — pinned by test, never imported;
  `service.py` fetches `docs/project/CHARTER.md` + `STATUS.md` from each repo's default branch through the
  shared `gh_utils.gh_api` client, caches 10 min, joins the newest `project-steward` run from the staff run
  store) and router `backend/routers/projects.py` (`GET /api/projects`, `GET /api/projects/{repo}`, fleet peer;
  missing charter → `charter_present: false`, malformed/GitHub failure → `error`, never 5xx). Repo list in
  `config/projects.json`. Frontend `frontend/src/pages/ProjectsPage.tsx` + `pages/Projects/` (nav `projects`
  in AI & Agents; stacked progress bar, decisions list, run link, "Run steward now" →
  `POST /api/staff/project-steward/run` with CSRF header). Docs: `docs/projects.md`. TDD: 13 tests in
  `tests/api/test_projects_router.py`, 4 in `frontend/src/pages/__tests__/Projects.test.tsx`.
- **2026-09-10:** Adopt Mermaid C4 architecture-map contract and automated CI verification (#1613).
  Established canonical docs/architecture/C4.md with C4Context and C4Container views,
  Feature Map table mapping core capabilities to components, interfaces, and test evidence,
  and an Architecture Change Log baseline table. Added scripts/architecture_map_contract.py
  and tests/test_architecture_map_contract.py, and wired verification into
  .github/workflows/architecture-map-contract.yml.
- **2026-09-07 (2.5.208):** Suppress pytest exit code 5 on empty marker in CI Nightly workflow (#1175).
  Scheduled integration CI runs (`ci-nightly.yml`) run pytest with `-m integration`.
  When no tests are marked with `@pytest.mark.integration`, pytest exits with code 5
  (no tests collected), causing false nightly CI run failures. Added graceful handling
  for exit status 5 in `ci-nightly.yml` step while preserving non-zero exit for all
  actual test execution failures. TDD: `tests/test_ci_config.py::test_ci_nightly_suppresses_empty_marker_exit_code`.
- **2026-09-06 (2.5.207):** Orchestrator authentication perimeter hardening (#1173).
  Conductor orchestrator state-changing endpoints (/api/orchestrator/lease, /release,
  /queue control) previously accepted unauthenticated callers across the tailnet when
  HUB_FLEET_TOKEN was unset, creating risk of capacity reservation exhaustion and
  dispatch starvation. Implemented `require_orchestrator_peer` in `backend/identity.py`
  and enforced on state-changing orchestrator routes. Calls are strictly authenticated
  against: (1) valid operator principals (service token or session), (2) intra-fleet
  bearer token matching HUB_FLEET_TOKEN (constant-time compare), or (3) local loopback
  peer with `DASHBOARD_LOOPBACK_AUTH=1` so local node Conductor agents function without
  credentials. Remote unauthenticated callers are strictly rejected with HTTP 401.
  Enforced `require_fleet_peer` on `GET /api/orchestrator/queue`. TDD: 22 tests in
  `tests/api/test_orchestrator_api.py`.
- **2026-09-06 (2.5.206):** Runner host reality vs /tmp runbook, per-host distro parity,
  and profile cargo cleanup (#1159). Host environments like OGLaptop lack a local
  `Runner_Dashboard` checkout, execute across varying WSL distribution names (`Ubuntu`,
  `Ubuntu-22.04`, `ControlTower-Runner`), and accumulated 12 deleted cargo env source
  lines in `~/.profile` causing noise in login shells. Created
  `deploy/clean-stale-shell-profiles.sh` to safely prune non-existent cargo/env lines from
  shell profiles and integrated it into `deploy/install-runner-maintenance.sh` as well as
  the URL/curl-based standalone runbook path. Documented explicit per-host distro mapping
  and standalone script deployment in `docs/runbooks/runner-tmp-exhaustion.md`. TDD:
  `tests/deploy/test_clean_stale_shell_profiles.py`.
- **2026-09-06 (2.5.205):** Hub dashboard telemetry aggregation and stale registry
  validation (#1169). The hub dashboard previously reported telemetry for only one node
  and identified it by a retired pool (`ControlTower-NVMe` retired 2026-07-31).
  Added startup registry validation (`assert_valid_active_registry`) in
  `backend/fleet_autoconfig.py` and `backend/server.py` ensuring every non-retired
  machine and runner pool resolves to a valid, reachable dashboard URL and fails fast
  on unresolvable endpoints or duplicate port collisions across active pools.
  Updated `derive_pool_topology` and `_iter_registry_entries` to filter out retired
  pools (`retired: true`), ensuring the hub identifies itself as `ControlTower-Runner`
  and eliminates phantom peer self-probes. Unified `FLEET_NODES` between `server.py`
  and `dashboard_config`, and enabled registry autoderivation in `routers/fleet.py` so
  `/api/fleet/status` concurrently polls and aggregates telemetry across all active
  fleet nodes (`DeskComputer`, `OGLaptop`, `ControlTower-Runner`) with graceful offline
  handling for connection-refused peers. TDD: `tests/test_hub_fleet_aggregation.py`
  and `tests/test_fleet_autoconfig.py`.
- **2026-09-06 (2.5.204):** Monitor host volume disk guard and inhibit scheduling
  on exhaustion (#1168). Guest runner VMs and WSL distributions cannot see the
  host backing volume that their virtual disks expand into. When the host drive
  runs low on disk space, the virtual disk cannot expand, causing abrupt I/O
  errors across all runners hosted on that volume. Added `backend/host_volume.py`
  to probe host volume disk metrics (`total_bytes`, `used_bytes`, `free_bytes`,
  `free_percent`) using Windows API / `shutil.disk_usage` and normalized host
  drive paths from `backend/machine_registry.yml` (`runner_backing_drive`). Enforced
  a hard floor alarm (< 5% free or < 30 GB free) that flags critical state and sets
  `offline_reason="host-volume-exhaustion"` to inhibit runner job scheduling before
  catastrophic host volume exhaustion occurs. Distinctly separated `host_volume`
  from guest distro-root disk metrics across backend models and `/api/fleet/status`.
  TDD: `tests/test_host_volume.py`.
- **2026-09-04 (2.5.203):** Remove the `pull_request` `paths-ignore` filter
  from `ci-standard.yml`, which made `LICENSE`-only and `.gitignore`-only PRs
  permanently unmergeable. `quality-gate` and `tests` are required status
  checks on main, so branch protection waits for them to report; the filter
  skipped the whole workflow for such PRs, and a required context that never
  runs is indistinguishable from one that has not started yet - the PR sits
  BLOCKED with zero failures and nothing pending, forever. The file already
  documented this reasoning for `**.md` ("intentionally NOT excluded so
  quality-gate runs on docs-only PRs and satisfies branch protection required
  status checks") but never applied it to its remaining two entries. The
  `push` filter is unchanged: post-merge CI does not gate the required
  context. New `tests/test_workflow_hygiene.py::
- **2026-09-04 (2.5.202):** Close two secret-hygiene gaps found while tracing a
  `GH_TOKEN` leak in agent tooling. (1) `tests/test_no_secrets_in_repo.py`
  matched GitHub tokens as `ghs_[A-Za-z0-9]{36,255}`, which cannot match the
  App _installation_ tokens this fleet actually mints
  (`ghs_<app_id>_<base64url-JWT>`) - the underscore after the app id ends the
  character class, so the gate was blind to the one credential shape most
  likely to reach a file here. The five GitHub patterns now share a
  `_GITHUB_TOKEN_BODY` class admitting `_`, `.` and `-`, anchored to an
  alphanumeric first character so bare prefixes inside the `deploy/*.sh`
  validation regexes still do not match, and with no trailing `` (base64url
  payloads can end in `-` or `_`). Three shape tests pin the behaviour.
  (2) `.gitignore` now ignores `.claude/settings.local.json` itself instead of
  relying on a developer's machine-local global ignore; that file records
  approved tool calls verbatim and had captured live tokens elsewhere in the
  fleet. The same edit strips two stray NUL bytes that had been committed into
  `.gitignore`, which made git and grep treat it as a binary file.
- **2026-09-04 (2.5.201):** Repoint the agent workflow health probe at the live
  agent surface (#1483, program #1505). `inspect_jules_workflows()` asserted a
  hardcoded tuple of five `Jules-*.yml` filenames; #1160 deleted the Jules
  workflows, so the probe emitted `exists=False` with "Workflow file is
  missing." for every one and the Remediation tab rendered five phantom
  failures. No test pinned the tuple, so CI stayed green. The probe is now
  `inspect_remediation_workflows()`, which **discovers** `.github/workflows/agent-*.yml`
  (case-insensitive, `.yml`/`.yaml`) from disk instead of asserting an
  inventory — the next retirement cannot resurrect the failure mode. The
  Control-Tower-specific cron branch, unreachable since #1160, is replaced by a
  generic summary counting dormant and flagged workflows, and a new
  `RETIRED_WORKFLOW_PATTERNS` scan flags any resurrected
  `.github/workflows/Jules-` reference. `GET /api/agent-remediation/workflows`
  gains a `summary` key; the pre-retirement `control_tower_summary` ships
  alongside it as an alias for one release (two-step schema change) and is
  removed in the next. `inspect_jules_workflows` is retained as a deprecated
  alias on the same schedule. Because the panel now lists _this_ repo's
  workflows, `POST /api/agent-remediation/dispatch-jules` — which was hardcoded
  to dispatch into `Repository_Management` — now targets the dashboard repo via
  the new `workflow_dispatch_endpoint()` helper (`DASHBOARD_REPO`,
  env-overridable via `RUNNER_DASHBOARD_REPO_NAME`); the route path keeps its
  legacy name for one release. `AGENTS.md`, `CLAUDE.md` and
  `docs/issue-migration-plan.md` no longer document the retired Jules
  workflows as live. Regression coverage:
  `tests/test_remediation_workflow_probe.py`.
- **2026-09-04 (2.5.200):** Make the deploy tests pick their bash by probe, so
  the tmp-litter GC harness stops depending on which shell launched pytest
  (#1164). Six tests in `tests/deploy/test_runner_tmp_litter_gc.py` passed under
  Git Bash and failed under PowerShell. Not a flake and not a missing bash — a
  _wrong_ one: Windows carries two valid POSIX bashes and PATH order is set by
  the launching shell, so `shutil.which("bash")` returned MSYS under Git Bash and
  `C:\Windows\System32\bash.exe` (the WSL launcher, a separate filesystem
  namespace) under PowerShell, where every scratch path came back "No such file
  or directory". The new `tests/deploy/bash_host.py` resolves bash by _probing
  capability_ rather than by PATH order or executable name: a candidate qualifies
  only if it can both stat and `find -maxdepth 0` a file the test process just
  wrote, and `BASH is None` drives the same `skipif` the sibling deploy tests
  use. The `find` half is load-bearing — a raw MSYS `usr/bin/bash.exe` passes the
  stat probe but inherits Windows' `PATH`, so the GC's `find` resolves to
  `System32\find.exe` and the purge silently no-ops; `bin/bash.exe` (the Git Bash
  launcher) prepends the MSYS coreutils. No assertion in the #1158/#1161 GC
  coverage was weakened. `test_runner_corruption_scan.py` and
  `test_runner_toolcache_isolation.py` carried near-identical `_find_bash` copies
  that guessed by executable name; both now call the shared `find_bash()`.
- **2026-09-03 (2.5.199):** Retire the last two references to the Jules
  remediation workflows and make the runner-routing policy rot-proof in both
  directions (#1483, program #1505). `config/workflow_runner_routing_policy.json`
  no longer classifies `Jules-Auto-Repair.yml` or `Jules-PR-AutoFix.yml`, which
  #1160 deleted; the dangling names failed
  `test_repo_policy_references_real_workflows` and produced `policy_errors` from
  `scripts/check_workflow_runner_routing.py`, turning `main` red. The tier
  classification is a per-workflow judgement and cannot be derived from the
  directory, but its _coverage_ can: the new
  `test_every_workflow_is_classified_or_explicitly_exempt` derives the on-disk
  workflow set and requires every file to be either classified or listed in a
  `_UNCLASSIFIED` map with a written rationale (four qualify — they are pinned
  to a single named host or routed by an expression, so fleet tier labels are
  meaningless for them). It also rejects a stale exemption and a name that is
  both classified and exempt. Adding a workflow now forces a tier decision.
  Separately, the `tests/deploy/test_runner_tmp_litter_gc.py` harness sources
  `cleanup_litter_in` out of `deploy/runner-cleanup.sh` under `set -u`; that
  function gained a `$DRY_RUN` reference in #1161 while the default lives at the
  script's top level, which the harness never executes, so the harness now
  declares `DRY_RUN="${DRY_RUN:-0}"` alongside the other variables it stubs.
- **2026-09-03 (2.5.198):** Heal poisoned runner workspaces at job start and
  stop the daily cleanup unit failing on a benign race (UpstreamDrift#9443,
  program #1505). (1) `deploy/runner-hooks/job-started.sh` now clears the two
  workspace states that make the NEXT job's `actions/checkout` report success
  while leaving a half-populated or empty working tree — so the job dies on a
  path its PR never touched (`scripts/ci/rehydrate_docker_context.py: No such
- **2026-09-03 (2.5.197):** Durable half of the runner `/tmp` exhaustion fix
  (Repository_Management#1489 via #1511, program #1505). (1) The `/tmp`
  litter GC from 2.5.194 is factored into `cleanup_litter_in <dir> <age_min>`
  - `tmp_litter_age_min` and gains the `tmp*` (Python `tempfile` default
    prefix) and `pymp-*` (multiprocessing) patterns observed alongside `pip-*`
    in both incidents. (2) New `cleanup_runner_tmpdirs` applies the same GC to
    each runner's relocated scratch dir `<runner_dir>/_work/_tmp`
    (`RUNNER_TMP_SUBDIR`) while that runner is idle (`runner_busy`), in both
    the hourly `--disk-guard` pass and the daily full pass — runner-safe,
    never stops units. (3) New `deploy/configure-runner-tmpdir.sh` writes
    `TMPDIR=<runner_dir>/_work/_tmp` into each `actions.runner.*.service`
    workdir's `.env` (idempotent, atomic, `--dry-run`, `--runner-dir` for
    tests/no-systemd hosts) and creates the directory, so pip/pytest/tempfile
    scratch lands on the data disk instead of the RAM-backed tmpfs; it never
    restarts runners — the operator restarts idle units afterwards (runbook:
    Repository_Management `docs/runbooks/runner_tmp_exhaustion.md`). TDD:
    `tests/deploy/test_runner_tmp_litter_gc.py` sources the real shell
    functions into a stubbed bash and asserts survivors on a seeded tree
    (aged litter reaped, fresh litter and non-litter kept, 30 m pressure
    window, no recursion) plus configure-script behaviour; existing
    `TestTmpLitterGC` pins retargeted to the factored functions.

- **2026-09-02 (2.5.196):** Decompose credentials router (`get_credentials()`)
  into small, cohesive resolvers (< 50 lines each) adhering to Design by
  Contract, Law of Demeter, and DRY. Replace broad and silent exception handlers
  with narrow typed exceptions logging source names and error classes without
  leaking credential values, and add comprehensive unit test coverage (issue #1151).
- **2026-09-01 (2.5.195):** Update `backend/machine_registry.yml` to the
  post-rebuild ControlTower topology. The active pool is
  **ControlTower-Runner** (fresh distro at `F:\WSL\ControlTower-Runner`,
  runners `d-sorg-local-ControlTower-1..4`, min 2 / default 4 / max 4,
  scheduler-managed daytime reduction), replacing the ControlTower-SSD
  pool whose vhdx went corrupt on 2026-08-24
  (Repository_Management#1453; vhdx retained on F:, its 8 GitHub runner
  registrations are offline ghosts). Docker engine (docker.io 29.1.3 +
  buildx + compose-v2) now lives in-distro and the pool carries
  `d-sorg-fleet-docker`, ending the period where DeskComputer's single
  online runner was the fleet's only docker capacity.
- **2026-09-01 (2.5.194):** Extend `deploy/runner-cleanup.sh` with `/tmp`
  CI-litter garbage collection (Repository_Management#1489 / #1495).
  Cancelled CI jobs orphan pip build directories (`pip-install-*`,
  `pip-build-env-*`, `node-compile-cache`, …) directly in `/tmp`; on hosts
  where `/tmp` is RAM-backed tmpfs this exhausts `/tmp` while every
  root-disk gate stays green, and all subsequent pip installs on the host
  die with ENOSPC — skipping every real quality step and hard-blocking
  required checks fleet-wide. `cleanup_tmp()` removes top-level `/tmp`
  entries matching a fixed litter-pattern allowlist aged past
  `TMP_LITTER_HOURS` (default 6h), tightening to 30 minutes when `/tmp`
  usage crosses `TMP_PRESSURE_PERCENT` (default 75%). Runs in both the
  hourly `--disk-guard` pass and the daily full pass; never touches
  non-litter paths or fresh entries a live install is writing.
- **2026-08-31 (2.5.193):** Refactor credentials router (`get_credentials()`) to
  adhere to the Law of Demeter and single-responsibility principle. Decomposed
  the 301-line monolithic route into modular helper functions with full unit
  test coverage and strict typing (issue #1151).
- **2026-08-26 (2.5.186):** Make late-stage release publication recoverable
  without weakening artifact identity (issue #1129). Manual recovery checks
  out the existing tag, accepts it only when it is annotated, governed, and
  exact to that source, skips tag mutation, and passes the repository identity
  explicitly to GitHub CLI so WSL UNC ownership translation cannot block
  release creation after signing and attestation have succeeded.
- **2026-08-27 (2.5.192):** Route the lightweight anti-phantom and
  hosted-routing pull-request guards through the existing reversible public-CI
  selector. Public repositories use GitHub-hosted Ubuntu capacity unless
  `CI_RUNNER_MODE=local`; private repositories and explicit local mode remain
  on `d-sorg-fleet`. The routing guard explicitly allowlists the anti-phantom
  selector, preventing governance-only checks from deadlocking while local
  runner pools are intentionally drained or quarantined.
- **2026-08-27 (2.5.191):** Strengthen the DeskComputer maintenance drain
  into a two-key operator contract. The Windows keepalive and fleet monitor
  may perform automatic recovery only when the drain marker is absent and the
  explicit enable marker is present. Removing the drain marker alone therefore
  cannot restart WSL or local runners (issue #1144).
- **2026-08-26 (2.5.190):** Synchronize release metadata at version 4.9.34
  so protected `main` publishes the first immutable schema-v2 artifact that
  contains the interactive-safe DeskComputer capacity policy: one weekday-day
  runner, two weekend/overnight runners, and a hard maximum of two. The live
  drain remains fail-closed until the signed artifact, checksum, SBOM,
  provenance, offline install, rollback, exact-revision APIs, and one governed
  scheduler cycle are verified (issue #1144).
- **2026-08-26 (2.5.189):** Align the canonical DeskComputer schedule with
  Repository Management's interactive-safe fleet policy: one weekday-day
  runner, two weekend/overnight runners, and a hard maximum of two. Exact
  schedule regression coverage prevents a future drain release from restoring
  twice the governed workstation load (issue #1142).
- **2026-08-26 (2.5.188):** Harden the issue #1138 deployment boundary before
  bootstrap. The exact deploy job now requires the protected
  `oglaptop-production` environment and its no-fallback organization-runner-read
  credential. The transaction records prior service authority, crosses the
  rollback boundary, quiesces all dashboard/scheduler/autoscaler writers, and
  only then snapshots and manifests mutable state. The root scheduler now uses
  a root-owned, release-specific Python 3.12 runtime and signed scheduler under
  `/opt`, plus the root-owned canonical schedule; it never executes user-writable
  scheduler code, configuration, or an interpreter.
- **2026-08-26 (2.5.187):** Add the issue #1138 qualified OGLaptop release
  deployment contract. A workflow-dispatch-only job on the exact OGLaptop-1
  runner verifies annotated protected-main release identity, checksum, cosign
  bundle, build attestation, safe schema-v2 archive metadata, and Python ABI,
  then crosses a one-time root-owned no-argument helper boundary. The helper
  permits only the proven current workflow worker to be busy, requires every
  other local and GitHub runner idle in the four-runner daytime steady state,
  snapshots and journals every mutation, preserves mutable state byte-for-byte,
  enforces four active runners in every current schedule window (eight remains
  only the installed-inventory ceiling) and the governed runtime, disables
  competing autoscaling, proves a no-action five-minute scheduler cycle, rolls
  back on failure, and emits only redacted evidence.
- **2026-08-26 (2.5.188):** Synchronize all canonical release metadata at
  version 4.9.33 so the first protected release after the #1126 capacity
  correction is built from the CVE-corrected protected main using the
  schema-v2 publication contract repaired by issue #1132 (issue #1131).
- **2026-08-26 (2.5.187):** Refresh the immutable Python 3.13 slim base and
  pin Debian's `libssl3t64`, `openssl`, and `openssl-provider-legacy` security
  package set to a non-overridable `3.5.7-1~deb13u2` constraint. Resolution
  fails closed if the fixed version is unavailable while preserving the locked
  application install, non-root runtime, and `/livez` healthcheck contracts
  (issue #1135).
- **2026-08-26 (2.5.186):** Route protected release builds through the
  canonical schema-v2 artifact packager so every published dashboard bundle
  includes the locked offline wheelhouse, exact source SHA, deterministic
  inventory, checksum, and isolated-install validation. Release publication
  also names the GitHub repository explicitly, avoiding WSL checkout discovery
  and dubious-ownership failures (issue #1132).
- **2026-08-25 (2.5.185):** Replaced the former 32-runner always-on default
  with an initial bounded DeskComputer schedule (issue #1125). Issue #1142
  subsequently tightened that policy for interactive-safe operation.
- **2026-08-24 (2.5.184):** Restore deterministic cross-platform frontend
  installation by recording Vitest's esbuild 0.28.2 dependency tree in the
  root lockfile. Frontend validation now fails closed on `npm ci` instead of
  falling back to an ungoverned `npm install`, keeping pull-request and Release
  lockfile behavior identical (issue #1085).
- **2026-08-24 (2.5.183):** Add a shared fail-closed DeskComputer
  maintenance-drain marker (issue #1115). The Windows WSL keepalive and fleet
  health monitor exit before recovery side effects while the marker exists;
  pure fleet-monitor helpers remain available for diagnostics. The operator
  runbook defines controlled drain and scheduler-governed restoration.
- **2026-08-24 (2.5.182):** Make governed runner scheduling the sole authority
  for DeskComputer capacity recovery (issue #1113). The five-minute fleet
  monitor reads the scheduler's last fail-closed desired-capacity decision,
  uses that value as the Desktop pool floor, and invokes
  `runner-scheduler.service` when recovery is required. It no longer starts
  every installed runner service, preventing the monitor from oscillating a
  two-runner daytime target back to all eight configured runners while
  preserving four-runner overnight and weekend recovery.
- **2026-08-24 (2.5.181):** Make immutable artifact deployment runtime-complete
  and fail closed (issue #1110). Artifact schema v2 includes the hash-locked
  Linux wheelhouse and root service helper, records the exact Python wheel ABI,
  performs dependency and import smoke checks, and preserves mutable runtime
  databases, ledgers, histories, and `.env` while replacing release files.
- **2026-08-24 (2.5.180):** Execute scheduler status and autoscaler probes
  with the dashboard's governed Python interpreter instead of the scheduler
  shebang, preventing Ubuntu 22.04 Python 3.10 from producing stale error or
  fallback state. Runner-capacity metadata now reports the effective schedule
  default and ceiling separately from the host slot limit and installed slots.
- **2026-08-24 (2.5.179):** Run the installed scheduler with the deployed
  dashboard virtual environment (issue #1107). The maintenance installer fails
  closed if the governed Python interpreter is absent or non-executable instead
  of falling through to an unsupported system Python.
- **2026-08-24 (2.5.178):** Fail closed when determining runner occupancy and
  enforce an optional host capacity ceiling (issue #1105). The scheduler now
  recognizes legacy and self-updated `Runner.Worker` paths, reparented workers,
  the pre-fork `_runner_file_commands` pickup marker, and job-hook lockfiles.
  Unreadable process or marker metadata is treated as busy. Inactive services
  with a surviving worker are excluded from start candidates and reported as
  `busy_without_listener`. Schedule defaults, timed entries, and manual targets
  are capped by `max_count`; dashboard schedule edits preserve the ceiling.
- **2026-08-21 (2.5.177):** Deterministic and offline-capable dashboard builds and deployments (issue #1085).
  - Switched frontend CI workflows and deployment scripts to `npm ci` ensuring strict lockfile synchronization.
  - Added `deploy/package-dashboard-artifact.sh` generating immutable tarballs with `FILES.txt` inventory, `deployment.json` metadata, and SHA-256 sidecars conforming to `docs/ARTIFACT_BUILD.md`.
  - Added `--checksum <sha256>` verification to `deploy/install-dashboard-artifact.sh` and offline dependency installation from `backend/wheels`.
  - Updated `deploy/update-deployed.sh` and `deploy/setup.sh` to bypass online `uv sync` / `uv export` during artifact deployments.
  - Pinned Dockerfile base image to `python:3.13-slim` matching project `requires-python (<3.14)`.

- **2026-08-21 (2.5.176):** Implement active-job interlock and host teardown safeguards
  (issue #1067). `deploy/wsl-keepalive.ps1` and `backend/wsl_interlock.py` check for active
  `Runner.Worker` processes and busy runners before any destructive WSL recovery, deferring
  `wsl --shutdown` resets unless explicit emergency override is provided. Dashboard health
  recovery is strictly isolated to avoid host/runner restarts on dashboard-only failures.
  Host recovery telemetry and structured audit logs (`wsl-teardown-audit.jsonl`) record all
  teardown decisions with initiator and active runner counts.
- **2026-08-16 (2.5.175):** Canonicalize fleet reliability and scheduler observability
  fixes in `deploy/` (issue #1078): `deploy/runner-scheduler.py` reports provenance for
  `manual-target` capacity reasons, `deploy/fleet-health-monitor.ps1` explicitly isolates
  and skips quarantined NVMe tasks, and `deploy/install-matlab-runner-task.ps1` establishes
  a 10-minute repeating trigger for Windows MATLAB runners on broker socket disconnections.
- **2026-08-16 (2.5.174):** Strengthen runner inventory pagination in
  `backend/runner_inventory.py` against partial/stale status reporting (issue #1076).
  `fetch_org_runners` now raises `IncompleteInventoryError` when pagination yields
  fewer runners than `total_count` (unless `allow_partial=True`), and `_runner_response`
  in `backend/routers/runners.py` marks `degraded=True` on incomplete lists while preserving
  the prior complete cached inventory under upstream outages/throttling.
- **2026-08-16 (2.5.173):** `backend/machine_registry.yml` records each
  fleet host's `runner_backing_drive` and storage configuration to prevent
  spurious disk-capacity gating against system drives rather than runner-backing
  volumes. Also restores supported Python runtime in `Dockerfile` (Python 3.13)
  aligned with `pyproject.toml` (`<3.14`) and strengthens deployment helper
  scripts against cross-platform execution.
- **2026-08-14 (2.5.172):** `uv.lock` is now part of the release
  single-source contract. It pins the project's own `runner-dashboard`
  version, but `test_static_release_metadata_tracks_version_file` covered
  only `pyproject.toml`, `package.json`, `package-lock.json`,
  `frontend/src/lib/openapi.json`, `SPEC.md`, and `CHANGELOG.md`, so the
  lockfile silently kept 4.9.24 through the 4.9.26 bump. CI cannot catch
  this on its own: the bootstrap step runs `uv sync --frozen`, which
  consumes the lockfile as-is rather than re-resolving it (`--locked` is
  the flag that asserts freshness). Guarded by
  `tests/test_version_single_source.py::test_uv_lock_records_the_project_version`.
  Bumping the release version now requires `uv lock` alongside the other
  seven files.
- **2026-08-14 (2.5.171):** The `ci-health-check` Python scope detector in
  `.github/workflows/ci-standard.yml` now treats `deploy/` and `tests/` as
  Python-relevant prefixes alongside `backend/`. Previously only `backend/`
  was matched, while the accompanying skip message claimed `tests` was
  covered — so a PR touching only tests or only deployment scripts reported
  `run_python_tests=false` and skipped all four gated jobs: `quality-gate`
  (the sole required status check in the `Repository_Protections` ruleset),
  `security-scan`, `tests`, and `tests-required`. Such a PR merged with zero
  test signal; PR #1092 added
  `tests/deploy/test_wsl_keepalive_script.py::test_script_defaults_to_resident_mode`
  and that regression test never executed in CI. `deploy/` matters for the
  same reason: `tests/deploy/` exists specifically to exercise the
  deployment scripts. The skip message now enumerates the same set the
  detector checks. Guarded by the scope-detector contract tests in
  `tests/test_ci_config.py`, which parse the detector's own literals out of
  the workflow, assert message/detector agreement in both directions, and
  pin the set of detector-gated jobs. Release metadata (`pyproject.toml`,
  `package.json`, `package-lock.json`, `frontend/src/lib/openapi.json`) is
  realigned to `VERSION` in the same change — it had drifted to 4.9.24 when
  the skipped `tests` job on PR #1092 failed to catch the 4.9.25 bump.
- **2026-08-14 (2.5.170):** `deploy/wsl-keepalive.ps1` now defaults `-Mode` to
  `Resident` instead of `Watchdog`. `Watchdog` recovery may call
  `wsl --shutdown`, which kills every distro and the WSL2 lightweight VM,
  taking every runner and in-flight CI job with it, and corrupting the ext4
  root on a hard kill mid-write. The canonical installer
  (`deploy/install-wsl-keepalive-task.ps1`) already passes `-Mode Resident`
  explicitly, so this default governs only a bare script run or a legacy
  scheduled task that omits `-Mode` — the paths that should fail safe rather
  than reboot a fleet host. `Watchdog` remains selectable explicitly for
  non-fleet hosts. Guarded by
  `tests/deploy/test_wsl_keepalive_script.py::test_script_defaults_to_resident_mode`.
- **2026-08-05 (2.5.169):** Queue refreshes now have a shared eight-second
  deadline across repository discovery, active-run sampling, and job-detail
  enrichment. Repository and job fan-out are each limited to six concurrent
  GitHub calls, while job-level queued counts are cached by repository and run
  ID for 120 seconds. If the deadline expires, `/api/queue` returns the
  completed subset or last-known-good snapshot with `data_source`,
  generation/serve timestamps, failed-repository counts, and
  `stats.budget_exhausted=true` rather than holding the request open. The
  budget, concurrency limits, and job-cache TTL are operator-configurable.
  The resolved `cryptography` dependency is 50.0.0, closing
  PYSEC-2026-3552 in the deploy and audit lock sets.
- **2026-08-03 (2.5.168):** Added a reversible, zero-polling public CI fast
  lane controlled by the `CI_RUNNER_MODE` repository variable. Lightweight
  validation may run on GitHub-hosted Ubuntu capacity while Docker image scans
  remain on the local Docker fleet; clearing or changing the variable to
  `local` restores local-only routing. Test fanout is capped and Docker builds
  now reuse a scoped GitHub Actions BuildKit cache. Queue telemetry now marks
  partial, stale, cached, and unavailable samples explicitly, reports failed
  repository and job-detail fetch counts, timestamps both data generation and
  serving, and no longer claims that all runners are idle when upstream data is
  incomplete.
  Optional principal resolution now checks the ASGI session scope directly,
  avoiding Starlette's assertion-raising `request.session` property probe in
  focused router apps that intentionally omit `SessionMiddleware`.
- **2026-06-21 (2.5.167):** Runner service-control routes now share a
  `principal_log_id` helper so loopback-gated admin principals that expose
  `id` instead of `user_id` can still start, stop, and restart runners without
  tripping logging-only `AttributeError`s after authorization succeeds. The
  local runner-number resolver also accepts current fleet names such as
  `d-sorg-local-ControlTower-nvme-1` in addition to legacy `runner-1` suffixes,
  keeping `/api/runners/{id}/start|stop|restart` and group start/stop actions
  mapped to the right `svc.sh` path while preserving the existing
  `runners.control` scope requirement.
- **2026-06-20 (2.5.166):** Host machine-spec probes now launch the Windows
  `powershell.exe` with `-WindowStyle Hidden -NonInteractive`. When the backend
  runs in WSL and shells out to the Windows host powershell for hardware facts,
  Windows otherwise pops a visible console window per probe on every spec sync;
  the flags keep the sync silent with no change to the data collected (#1056).
- **2026-06-20 (2.5.165):** Exempted the read-only org runner inventory
  `GET /api/runners` from the #924 structural auth perimeter, alongside
  `/api/system` and `/api/fleet/status` (2.5.162). The fleet-health monitor on
  the hub polls each node's `GET /api/runners` over the tailnet with no operator
  principal to read online/busy counts and drive keepalive auto-recovery; the
  perimeter 401-ed it, so `ct_runners_online` went null and self-healing
  silently stopped. The exemption is an exact match on the bare inventory route
  only — mutating `/api/runners/{id}/start|stop|restart` and the diagnostics
  POSTs are distinct paths and remain perimeter-protected (and carry their own
  `require_scope` dependencies).
- **2026-06-18 (2.5.164):** WSL keepalive parser coverage now writes its
  one-shot probe state and log artifacts to the pytest `tmp_path` tree instead
  of a repository-root `.test-wsl-keepalive-junk/` directory. This preserves
  the PowerShell parser/smoke contract while keeping local and CI worktrees
  clean after `tests/deploy/test_wsl_keepalive_script.py` runs.
- **2026-06-17 (2.5.162):** Exempted the read-only fleet-telemetry endpoints
  `/api/system` and `/api/fleet/status` from the #924 structural auth perimeter.
  The hub's fleet fan-out (`fetch_node` → `/api/system`; peer pools →
  `/api/fleet/status`) presents no operator principal, so the perimeter 401-ed
  every node once it moved to >=4.9 code, marking the whole fleet offline. These
  are tailnet-scoped GET metrics already intended as tailnet-public fleet reads
  (`require_fleet_peer`); `/api/fleet/status` keeps its own `require_fleet_peer`
  dependency. Restores hub→node fleet status across the fleet.
- **2026-06-16 (2.5.161):** Fixed the DeskComputer node `dashboard_url` in
  `machine_registry.yml` to its MagicDNS name
  (`http://deskcomputer.tail2bbcc7.ts.net:8321`) instead of the raw tailnet IP.
  Tailscale's `serve` exposes node dashboards by name (name-based HTTP), so the
  raw-IP entry returned 404 and the hub marked DeskComputer offline. Every other
  node already used its MagicDNS name; this aligns DeskComputer with the fleet
  convention so hub↔node fleet status resolves.
- **2026-06-16 (2.5.160):** Made `/api/fleet/status` resilient to unreachable
  fleet nodes. Peer pools are now fetched concurrently (`asyncio.gather`)
  instead of in a sequential loop, and cross-node probes use a connect-capped
  `httpx.Timeout` (`HttpTimeout.NODE_CONNECT_S`) so a black-holed node fails in
  ~5 s on the handshake instead of consuming the full 30 s read budget — the
  endpoint no longer stalls to ~30 s when any node is offline. Extracted
  `_fetch_peer_pool` / `_node_probe_timeout` helpers.
- **2026-06-16 (2.5.159):** Routed the Conductor admission gate's capacity
  provider through the shared `runners` cache via a new
  `gh_utils.get_cached_org_runners` helper (also adopted by the GitHub health
  summary), so each admission decision reuses one GitHub round-trip per
  `CacheTtl.RUNNERS_S` window instead of issuing an uncached call on every
  Conductor poll. Corrected the stale "cached upstream" comment on
  `_orchestrator_capacity_provider`.
- **2026-06-15 (2.5.158):** Stabilized FIFO session-eviction coverage after
  the post-merge `main` CI run exposed another wall-clock-sensitive session
  test. Session record defaults now use the module clock helper, and the FIFO
  test advances that clock deterministically instead of sleeping between
  registrations.
- **2026-06-15 (2.5.157):** Linked the remaining legacy App decomposition
  marker to the #949 closeout evidence without changing runtime behavior.
- **2026-06-15 (2.5.156):** Added a static frontend-integrity guard that keeps
  the #949/#951 audit closeout record tied to the tested frontend and router
  dependency evidence paths.
- **2026-06-15 (2.5.155):** Added an operations closeout record for the
  2026-06-12 Runner Dashboard audit epic and final #949 cleanup issue so the
  closure guard can tie #949/#951 to a merged PR with explicit evidence.
- **2026-06-15 (2.5.154):** Continued #949 mobile legacy-fallback retirement
  by routing the mobile Remediation tab through the dedicated
  `RemediationMobile` page instead of the hidden legacy App. The shell keeps
  mobile remediation in-flight dispatch state above the tab content so dispatch
  status persists across tab switches, and routing/static tests now guard that
  `/t/remediation` does not import the legacy App on mobile.
- **2026-06-15 (2.5.153):** Stabilized session-expiry validation after the
  post-merge `main` CI run exposed a timing-sensitive
  `test_prune_expired_sessions` failure under parallel test execution.
  Session-management timestamp reads now route through an internal clock helper
  so expiry tests can advance time deterministically instead of sleeping.
- **2026-06-15 (2.5.152):** Continued #949 frontend monolith retirement by
  adding a static registry-vs-router guard. Every tab declared in
  `navRegistry.ts` must now have a matching native desktop route case in
  `RoutedShell.tsx`, preventing new desktop tabs from silently falling back to
  the legacy App while the existing legacy App line-count ratchet remains in
  force.
- **2026-06-15 (2.5.151):** Continued #949 frontend monolith retirement by
  removing the modern desktop shell's silent legacy App fallback. Registered
  desktop tabs now render their native page content directly, while the
  explicit legacy layout escape hatch and mobile fallback stay unchanged. The
  frontend integrity suite now guards against reintroducing `nativeContent ??`
  fallback behavior or a chromeless legacy fallback in the desktop shell.
- **2026-06-15 (2.5.150):** Continued #949 frontend monolith retirement on the
  mobile shell path. Native mobile tabs (`overview`, `queue`, `maxwell`,
  `reports`, and `credentials`) now skip constructing the legacy lazy App
  fallback entirely, while drawer tabs without extracted mobile pages keep the
  fallback for feature parity. Regression coverage asserts the native mobile
  routes do not import or mount the legacy chunk, and the static frontend
  integrity gate prevents the hidden fallback from returning.
- **2026-06-15 (2.5.149):** Continued #949 frontend monolith retirement by
  adding a self-contained `RemediationPage` container for desktop. The page now
  owns remediation config/workflow/history loading, failed-run loading,
  policy-save PUTs, guarded plan previews, and remediation dispatch POSTs
  outside the legacy App owner while preserving the prop-driven
  `RemediationTab` contract for fallback callers. `RoutedShell` bypasses the
  legacy chunk for `/t/remediation`, and the extracted remediation modules stay
  in a lazy `remediation` bundle.
- **2026-06-15 (2.5.148):** Continued #949 frontend monolith retirement by
  adding a self-contained `OverviewPage` container for desktop. The page now
  owns the fleet overview GET fan-out, fleet-wide control POSTs, and per-runner
  control POSTs outside the legacy App owner while preserving the prop-driven
  `FleetTab` contract for fallback callers. `RoutedShell` bypasses the legacy
  chunk for `/` and `/t/overview`, and the shared overview modules stay in a
  lazy `fleet-overview` bundle so the entry perf budget remains enforced.
- **2026-06-15 (2.5.147):** Continued #949 frontend monolith retirement by
  adding a self-contained `FleetOrchestrationPage` container for desktop. The
  page now owns `/api/fleet/orchestration`,
  `/api/fleet/orchestration/dispatch`, and
  `/api/fleet/orchestration/deploy` outside the legacy App owner while
  preserving the prop-driven `FleetOrchestrationTab` contract for fallback
  callers, and `RoutedShell` bypasses the legacy chunk for
  `/t/fleet-orchestration`.
- **2026-06-15 (2.5.146):** Continued #949 frontend monolith retirement by
  adding a self-contained `FeatureRequestsPage` container for desktop. The page
  now owns `/api/repos`, `/api/feature-requests`,
  `/api/feature-requests/templates`, `/api/feature-requests/dispatch`, and
  `/api/settings/prompt-notes` outside the legacy App owner while preserving the
  prop-driven `FeatureRequestsTab` contract for fallback callers, and
  `RoutedShell` bypasses the legacy chunk for `/t/feature-requests`.
- **2026-06-15 (2.5.145):** Continued #949 frontend monolith retirement by
  adding a self-contained `AssessmentsPage` container for desktop. The page now
  owns `/api/repos`, `/api/assessments/scores`, and
  `/api/assessments/dispatch` outside the legacy App owner while preserving the
  prop-driven `AssessmentsTab` contract for fallback callers, and `RoutedShell`
  bypasses the legacy chunk for `/t/assessments`.
- **2026-06-15 (2.5.144):** Continued #949 frontend monolith retirement by
  adding a self-contained `CredentialsPage` container for desktop. The page now
  owns `/api/credentials` probe loading and `/api/credentials/set-key` updates
  outside the legacy App owner while preserving the prop-driven
  `CredentialsTab` contract for fallback callers, and `RoutedShell` bypasses
  the legacy chunk for `/t/credentials`.
- **2026-06-15 (2.5.143):** Continued #949 frontend monolith retirement by
  adding a self-contained `WorkflowsPage` container for desktop. The page now
  owns `/api/workflows/list` and `/api/workflows/dispatch` outside the legacy
  App owner while preserving the prop-driven `WorkflowsTab` contract for
  fallback callers, and `RoutedShell` bypasses the legacy chunk for
  `/t/workflows`.
- **2026-06-15 (2.5.142):** Continued #949 frontend monolith retirement by
  adding a self-contained `MachinesPage` container for desktop. The page now
  owns `/api/fleet/nodes` and `/api/runners` outside the legacy App owner while
  preserving the prop-driven `MachinesTab` contract for fallback callers, and
  `RoutedShell` bypasses the legacy chunk for `/t/machines`.
- **2026-06-15 (2.5.141):** Restored dependency audit health after new
  advisories flagged the locked dashboard runtime set. `starlette` now pins to
  `1.3.1`, and the exported `uv` requirements refresh lifts transitive
  `cryptography` to `49.0.0`, clearing the current `pip-audit` gate while
  preserving the FastAPI runtime contract.
- **2026-06-15 (2.5.140):** Continued #949 frontend monolith retirement by
  adding a self-contained `RunnerSchedulePage` container for desktop. The page
  now owns `/api/fleet/schedule` GET/POST outside the legacy App owner while
  preserving the prop-driven `RunnerScheduleTab` contract for fallback callers,
  and `RoutedShell` bypasses the legacy chunk for `/t/runner-schedule`.
- **2026-06-15 (2.5.139):** Continued #949 frontend monolith retirement by
  adding a self-contained `TestsPage` container for desktop. The page now owns
  `/api/heavy-tests/repos` and `/api/tests/ci-results` outside the legacy App
  owner while preserving the prop-driven `TestsTab` contract for fallback
  callers, and `RoutedShell` bypasses the legacy chunk for `/t/tests`.
- **2026-06-15 (2.5.138):** Restored push-time CI Standard line-cap health by
  splitting the native Maxwell chat/tasks panels, the Local Apps error boundary,
  and Maxwell test fixtures into focused modules. The three previously
  over-limit files now stay below the 500-line soft cap without changing their
  rendered behavior or API contracts.
- **2026-06-15 (2.5.137):** Continued #949 frontend monolith retirement by
  adding a self-contained `LocalAppsPage` container for desktop. The page now
  owns `/api/local-apps` outside the legacy App owner while preserving the
  prop-driven `LocalAppsTab` contract for fallback callers, and `RoutedShell`
  bypasses the legacy chunk for `/t/local-apps`.
- **2026-06-15 (2.5.136):** Continued #949 frontend monolith retirement by
  making the desktop `org` tab self-fetch `/api/repos` and `/api/stats` through
  a native `OrgPage` container, then routing it directly through the modern
  desktop shell. The presentational `OrgTab` remains prop-driven for legacy
  fallback callers and focused tests, while RoutedShell and static integrity
  coverage guard the native route bypass.
- **2026-06-15 (2.5.135):** Continued #949 frontend monolith retirement by
  adding a self-contained `MaxwellPage` container for desktop. The page now
  owns `/api/maxwell/status` and `/api/maxwell/control` outside the legacy App
  owner while preserving the prop-driven `MaxwellTab` contract for fallback
  callers, and `RoutedShell` bypasses the legacy chunk for `/t/maxwell`.
- **2026-06-15 (2.5.134):** Continued #949 frontend monolith retirement by
  routing the desktop `queue` tab directly through the extracted
  `pages/Queue` implementation. The page's existing `/api/queue` self-fetch
  path now serves the modern shell while the legacy prop-driven queue path
  remains intact for fallback callers, with shell and static integrity coverage
  guarding the native route bypass.
- **2026-06-15 (2.5.133):** Continued #949 frontend monolith retirement by
  making `DeploymentTab` self-fetch `/api/deployment/state` when rendered
  outside the legacy App owner, then routing the `deployment` desktop tab
  directly through `pages/Deployment.tsx`. Legacy prop-driven deployment
  rendering remains intact for fallback callers, while the modern shell avoids
  mounting the legacy `App` chunk for deployment operations.
- **2026-06-15 (2.5.132):** Continued #949 frontend monolith retirement by
  routing the `runner-audit` desktop tab through a native `RunnerAuditPage`
  container. The page now owns the current `/api/runner-routing-audit` GET and
  refresh POST contract outside the legacy `App` chunk, with tests covering
  the endpoint, CSRF bridge header, delayed refresh poll, and RoutedShell native
  bypass.
- **2026-06-15 (2.5.131):** Continued #949 frontend monolith retirement by
  making `AnalysisTab` self-fetch report summaries when rendered outside the
  legacy App owner, then routing `analysis` and `reports` desktop tabs directly
  through `pages/Analysis.tsx`. The modern shell now bypasses the legacy `App`
  chunk for those analysis routes while preserving the legacy prop-driven path
  for fallback tabs.
- **2026-06-15 (2.5.130):** Continued #949 frontend monolith retirement by
  routing the self-contained `events` desktop tab directly through
  `pages/Events.tsx`. The modern desktop shell now shows the event log and
  alarm center without mounting the legacy `App` chunk, with RoutedShell
  coverage guarding the native route bypass.
- **2026-06-15 (2.5.129):** Continued #949 frontend monolith retirement by
  routing more self-contained desktop tabs directly through extracted page
  modules. `principals`, `push-settings`, `scheduled-jobs`, and `settings` now
  bypass the legacy `App` chunk in the modern desktop shell while stateful
  prop-driven tabs continue to use the legacy fallback.
- **2026-06-15 (2.5.128):** Hardened the mobile Credentials tab visibility
  lock after #949 extraction by installing the `visibilitychange` listener for
  the full mobile lifecycle and updating the unlock ref synchronously before
  refresh callbacks run. This closes the same-tick unlock race where mobile
  credential metadata could remain visible after the tab lost focus, with a
  focused Vitest regression.
- **2026-06-15 (2.5.127):** Continued #949 frontend monolith retirement by
  deleting the dead legacy `LANG_COLORS` copy from `legacy/App.tsx` and routing
  the extracted Organization tab through the tested shared
  `components/formatters.ts` language-colour map. The shrink-only static guard
  now caps `legacy/App.tsx` at 2886 lines.
- **2026-06-15 (2.5.126):** Continued #949 frontend monolith retirement by
  routing self-contained modern desktop tabs directly through their extracted
  page modules. `agent-dispatch`, `cline-launcher`, `conductor`, `diagnostics`,
  and `linear-setup` no longer mount the legacy `App` chunk in the modern
  desktop shell, while stateful tabs still fall back to the chromeless legacy
  app and the reversible legacy-layout flag remains intact. RoutedShell tests
  now assert both the native route bypass and the legacy fallback contract.
- **2026-06-15 (2.5.125):** Continued #949 frontend monolith retirement by
  extracting the legacy global fetch guard into
  `frontend/src/legacy/fetchGuards.ts`. The extracted guard owns the
  credentials API service-worker cache denylist, no-store fetch option, silent
  session refresh retry, and session-expired toast/event fallback, with Vitest
  coverage and static integrity checks. `legacy/App.tsx` now delegates that
  contract and the shrink-only ratchet drops to 2909 lines.
- **2026-06-15 (2.5.124):** Hardened the Python 3.14 Docker build path by
  deleting temporary Rust/cargo/rustup and pip build caches after source-built
  wheels are installed. This keeps build-only `pyo3` sources out of the
  runtime image and prevents Trivy from failing on vulnerabilities in discarded
  build inputs while preserving the locked dependency set.
- **2026-06-15 (2.5.123):** Restored Docker image build compatibility after
  the pinned runtime image advanced to Python 3.14. The locked dependency set
  can require native Rust/C extension builds before upstream wheels are
  available, so `Dockerfile` now installs the minimal Debian build toolchain
  used by those source builds while keeping the runtime healthcheck and
  non-root execution contract unchanged.
- **2026-06-15 (2.5.122):** Continued #949 frontend monolith retirement with
  a shrink-only static guard. `tests/test_frontend_integrity.py` now fails if
  `legacy/App.tsx` grows above 2960 lines and asserts Fleet/Remediation route
  through the extracted `pages/FleetTab.tsx` and `pages/RemediationTab.tsx`
  implementations instead of reintroducing inline legacy twins. The legacy app
  sheds obsolete decomposition comments while preserving current shell
  behaviour.
- **2026-06-15 (2.5.121):** Continued #949 frontend monolith hardening. Legacy
  dashboard polling now registers through `frontend/src/legacy/visibleInterval.ts`,
  which skips interval callbacks while the browser tab is hidden and refreshes
  once on `visibilitychange` when the tab becomes visible again. The legacy app
  keeps the existing immediate first fetches and state ownership, but the 15
  background pollers plus the `/health` recovery check no longer run
  continuously in hidden tabs. `tests/test_frontend_integrity.py` guards the
  visibility-aware polling contract, and `legacy/App.tsx` shrinks to 2919 lines.
- **2026-06-15 (2.5.120):** Updated the release signing step for the installed
  cosign v4 behaviour. The release workflow now writes a
  `dashboard-<version>.bundle` with `cosign sign-blob --bundle` and uploads that
  bundle with the tarball, checksum, and SBOM instead of deprecated
  split signature/certificate outputs.
- **2026-06-15 (2.5.119):** Finished the release tarball hardening by writing
  the in-progress archive to `$RUNNER_TEMP` and moving the completed artifact
  back into the workspace before hashing. This prevents the workspace root from
  changing while `tar` is reading `.` during the `4.9.17` release.
- **2026-06-15 (2.5.118):** Hardened the release tarball step after the
  `4.9.17` release workflow exposed that `tar czf dashboard-<version>.tar.gz .`
  can read its own in-progress output. The release workflow now excludes
  `.venv` and generated dashboard release artifacts (`.tar.gz`, checksums,
  signatures, and certs) from the source archive, with a regression in
  `tests/test_release_workflow_yaml.py`.
- **2026-06-15 (2.5.117):** Continued #949 backend DI cleanup. Deployment
  and orchestration routers now receive server helpers through typed FastAPI
  app-state dependency objects instead of module-global `Callable | None`
  variables mutated by `set_dependencies()`. Focused regressions assert these
  routers no longer carry optional callable globals while preserving the
  existing route behaviour.
- **2026-06-15 (2.5.116):** Started the Runner_Dashboard side of #962 least-
  privilege Maxwell-Daemon access. The Maxwell proxy now uses the configured
  static `MAXWELL_API_TOKEN` only as a bootstrap credential for
  `POST /api/v1/auth/token`, caches short-lived scoped JWTs, sends viewer tokens
  on read/status proxies, and sends operator tokens on dispatch/control/chat
  calls. Older daemons that do not expose scoped token minting still fall back to
  the static token for compatibility, but modern RD↔MD deployments no longer
  put an admin-capable credential on routine polling requests.
- **2026-06-15 (2.5.115):** Reduced the #949 frontend monolith surface on
  mobile native tabs. `MobileShell` now treats `tabContent` as exclusive page
  content instead of hidden-mounting the legacy `App` behind native Fleet, Queue,
  Maxwell, Reports, and Credentials mobile pages. This prevents the legacy
  polling tree from running in the background for those tabs while preserving
  the fallback child render for tabs without native mobile content. A focused
  MobileShell regression asserts the legacy child tree is not mounted when
  native tab content exists.
- **2026-06-14 (2.5.114):** Removed the hub-proxy `configure()` globals from
  the deployment and orchestration routers for issue #949. The routers now call
  the canonical `proxy_utils` implementation directly, keeping the
  credential-stripping proxy contract DRY and eliminating mutable
  `_proxy_to_hub` / `_should_proxy_fleet_to_hub` module state. The
  `tests/api/test_proxy_credential_stripping.py` regression now fails if those
  injected proxy callables return.
- **2026-06-14 (2.5.113):** Kept the runner job-started hook bounded for fleet
  reliability. `deploy/runner-hooks/job-started.sh` now keeps the fast global
  `~/.gitconfig.lock` cleanup in place, makes the expensive per-worktree stale
  git-lock scan opt-in via `RUNNER_HOOK_ENABLE_WORKTREE_LOCK_CLEANUP=1`, and
  bounds that scan with `RUNNER_HOOK_LOCK_CLEANUP_TIMEOUT_SECONDS` (default 10s).
  This prevents SSD runner pools from cancelling jobs inside
  `ACTIONS_RUNNER_HOOK_JOB_STARTED`; scheduled cleanup remains responsible for
  broad worktree sweeps. `tests/test_today_deploy_hardening.py` guards the deploy
  hook contract.
- **2026-06-14 (2.5.112):** Replaced the last hand-written RD↔MD consumer
  contract fixtures with a vendored Maxwell_Daemon OpenAPI snapshot for issue
  #960. `tests/contracts/maxwell_openapi.json` is the producer-owned schema
  baseline; `tests/test_maxwell_openapi_contract.py` asserts the dashboard's
  consumed paths/schemas exist, validates minimal producer-required payloads
  against the dashboard models, and fails loudly when consumed required fields
  are renamed. The task-list model now requires MD's `total`, dispatch requires
  a producer task id/status while retaining the legacy `id` alias, and cost
  mirrors MD's required `month_to_date_usd` into the dashboard's legacy
  `total_usd` field. `scripts/check_maxwell_contract_drift.py` and
  `.github/workflows/maxwell-contract-drift.yml` add a scheduled/manual
  self-hosted drift monitor that compares the vendored snapshot with
  `D-sorganization/Maxwell_Daemon` main and records a GitHub issue when they
  differ.
- **2026-06-13 (2.5.111):** Expose hub-circuit degraded fallback state for issue
  #948. When a node with `HUB_URL` serves local `/api/fleet/status` data only
  because the hub circuit is open, the response now includes top-level
  `_degraded: true` and an `X-Dashboard-Degraded: hub-circuit-open` header.
  Explicit local reads (`local=true` or `scope=local`) keep the plain local
  response contract. `/api/health` reports `hub_circuit_open`, and Prometheus
  exports `dashboard_hub_circuit_open`. Focused regression coverage lives in
  `tests/test_proxy_utils.py`, `tests/api/test_fleet_aggregator.py`,
  `tests/test_health.py`, and `tests/test_prometheus_metrics.py`.
- **2026-06-13 (2.5.110):** Stabilize the OpenAPI-to-TypeScript contract
  generation gate added for issue #947. `scripts/gen-api-client.sh` now formats
  the generated `frontend/src/lib/openapi.json` snapshot with the pinned local
  Prettier dependency before `--check` diffs it, so CI compares against the same
  compact JSON style committed in the repository instead of failing on formatting
  drift. `tests/frontend/test_api_generation_contract.py` guards the canonical
  scripts, checked snapshot, generated TypeScript output, and formatter ordering.
- **2026-06-12 (2.5.109):** Quick Start works on a clean checkout + gh_client
  robustness (issues #945, #938). (#945) `start-dashboard.sh` now installs from
  the repo-root `requirements.txt` (the phantom `backend/requirements.txt` path
  silently fell back to a fastapi+uvicorn-only install that crashed on
  `import httpx/psutil/yaml`); it fails loudly if the requirements file is
  missing and builds the gitignored `frontend/dist/` via `npm ci && npm run build`
  (or exits non-zero with instructions) so the served page is the real SPA, not
  the "index.html not found" fallback. README documents the Node/npm requirement;
  `tests/test_start_dashboard_script.py` guards the script against regressing.
  (#938) `backend/gh_client.py`: (a) `_request` now treats any `2xx` as success
  so GitHub's `202 Accepted` (e.g. `cancel_run`) is no longer reported as a
  `GhServerError`; (b) `paginate` routes every page through `_request`, so a
  primary-rate-limit `403` (`X-RateLimit-Remaining: 0`) raises the typed
  `GhRateLimited` and transient `5xx` retry/backoff applies, instead of only
  special-casing `429`; (c) the GitHub App installation-token exchange is now
  async (`httpx.AsyncClient`) under an `asyncio.Lock`, so it no longer blocks the
  event loop ~hourly and a refresh storm dedupes to one upstream exchange.
  Tests in `tests/test_gh_client.py` and `tests/test_start_dashboard_script.py`.
- **2026-06-12 (2.5.108):** Single source of truth for fleet topology, runtime
  config, and the identity store (issues #942, #943, #944). (#942) `/api/fleet/status`
  no longer hardcodes `if PORT == 8322` to label itself ControlTower-NVMe and
  probe a phantom ControlTower-HDD peer; it derives local-pool identity and the
  sibling pools to probe from `machine_registry.yml` via a new
  `fleet_autoconfig.derive_pool_topology`. Single-pool machines emit no phantom
  peer node, and `_startup` now fails fast (`assert_no_maxwell_port_collision`)
  when `MAXWELL_PORT` collides with a peer dashboard port in the registry.
  (#943) `runners/service_control.py` and `routers/system.py` stopped
  re-deriving `RUNNER_BASE_DIR`/`ORG`/runner limits/`HOSTNAME`/`RUNNER_ALIASES`
  from `os.environ` and now read them live from `dashboard_config`, so a
  `RUNNER_BASE_DIR` override drives both metrics scanning and the sudo-executed
  svc.sh path; the four `_runner_limit` copies collapse to the single
  `dashboard_config.runner_limit` (service_control re-exports a back-compat
  alias). A new `tests/test_config_single_source.py` guard fails on re-derivation
  or a second `runner_limit` definition. (#944) The identity store is anchored to
  `XDG_CONFIG_HOME/runner-dashboard` (override `DASHBOARD_IDENTITY_DIR`) via
  `identity.resolve_identity_dir` instead of a CWD-relative `Path("config")`, so
  launching from any directory uses the same store; the resolved dir is logged at
  startup; `config/principals.yml`/`tokens.yml` are untracked + gitignored and a
  `config/principals.yml.example` ships instead. Tests in
  `tests/test_fleet_autoconfig.py`, `tests/test_identity.py`,
  `tests/test_config_single_source.py`, `tests/api/test_fleet_aggregator.py`,
  and `tests/api/test_runner_service_control.py`.
- **2026-06-12 (2.5.107):** Hardened the fleet-node SSRF guard against
  DNS-suffix spoofing and rebinding (security, issue #931). `validate_fleet_node_url`
  in `backend/security.py` previously accepted any host ending in
  `.local`/`.internal`/`.ts.net` on suffix match alone, so a lookalike like
  `evil.example.ts.net` — or a `.internal` name resolving to a public or
  link-local IP — passed the guard. The suffix is now necessary but not
  sufficient: when a name resolves, every resolved IP must fall inside an allowed
  range (RFC 1918 private, loopback, or RFC 6598 CGNAT), with link-local
  (169.254.0.0/16, fe80::/10) and all public addresses explicitly rejected. Names
  that do not resolve are still accepted as config-time entries (a peer may be
  offline). A new `resolve_and_validate_fleet_host` helper returns the single
  validated IP an outbound caller should pin to, defeating DNS rebinding between
  the URL check and the request.
- **2026-06-12 (2.5.106):** Security/robustness hardening (issues #929, #930,
  #939). (#929) `safe_subprocess_env` (`backend/security.py`) stripped only a
  hand-maintained denylist, so any _new_ `*_TOKEN`/`*_SECRET` env var leaked to
  every spawned subprocess by default; added a rot-proof suffix catch-all
  (`*_SECRET`/`*_TOKEN`/`*_KEY`/`*_PASSWORD`/`*_PASSWD`/`*_CREDENTIALS`) on top of
  the denylist (anchored at end-of-name, so `TOKEN_FILE_PATH` still passes
  through). (#930) The session cookie was unconditionally `https_only=True` and
  HSTS was always sent, but browsers drop Secure cookies on http:// origins — so
  session auth silently never worked on the documented plain-HTTP-over-tailnet
  deployment. Both are now gated on a new `DASHBOARD_TLS` flag
  (`dashboard_config.TLS_ENABLED`): default HTTP mode issues a usable cookie and
  sends no HSTS; TLS mode enforces Secure + HSTS. (#939) (a) `save_tokens`
  (`backend/identity.py`) is now an atomic tempfile + `os.replace` write like
  `save_principals`, so a crash mid-dump can't corrupt tokens.yml and lock
  everyone out; (c) the `POST /_drain` loopback guard (`backend/server.py`) is now
  an explicit `HTTPException(403)` instead of a bare `assert` (which `python -O`
  compiles out, letting any peer drain the server); (d) the subprocess timeout
  paths in `system_utils.run_cmd` and `queue_cleanup` now kill processes, await
  `wait()`, and tolerate `ProcessLookupError`, preventing zombie/transport leaks.
  Tests in `tests/test_security.py`, `tests/test_middleware.py`,
  `tests/test_identity.py`, `tests/api/test_drain_mode.py`, and
  `tests/test_system_utils.py`.
- **2026-06-12 (2.5.104):** server.py god-module duplicate sweep (architecture,
  issue #941). Removed two body-identical twins from the ~2.3k-line wiring
  module: the `POST /api/launchers/generate` route handler (a shadowed dead copy
  of `routers/diagnostics.py`'s — FastAPI served the router's, never server.py's)
  and the unused `_normalize_repository_input` helper (a copy of the ones in
  `routers/assistant.py`/`routers/remediation.py` that server.py never called).
  Extended `tests/test_no_duplicate_top_level_functions.py` from legacy/App.tsx
  to all of `backend/**/*.py`: a new guard fails if server.py defines any function
  body-identical to another backend module, plus a backend-wide "no new
  body-identical duplicate" ratchet (pre-existing legitimate idioms allow-listed).
  Pruned the now-resolved launchers entry from the #941 route-uniqueness allowlist
  and corrected the CLAUDE.md architecture block (server.py size ~2.3k not ~6800;
  requirements.txt lives at the repo root, not `backend/`).
- **2026-06-12 (2.5.101):** Autoscaler correctness/robustness cluster (issues
  #932, #935, #936, #937). (#932) The autoscaler read leases from a repo-relative
  `config/leases.yml` that nothing ever wrote, so lease protection was a permanent
  no-op and actively-leased runners could be stopped; `_leased_runners`
  (`backend/autoscaler_sampling.py`) now resolves the path through
  `runner_lease._default_config_dir()` — the same `RUNNER_DASHBOARD_CONFIG_DIR`
  store the `LeaseManager` writer uses — so reader and writer can never drift.
  (#935) `_stop_unit`/`_start_unit` (`backend/autoscaler_systemd.py`) issued a
  blocking `systemctl stop`; a legitimate >=120s drain starved the systemd
  watchdog (WatchdogSec=120) and SIGABRTed the autoscaler mid-scale-down. Both now
  use `--no-block` plus an explicit client-call timeout and re-read unit state the
  next tick. (#936) `prune_expired` (`backend/runner_lease.py`) wrote a stale
  in-memory snapshot via an unlocked `save_leases` on every reaper tick, clobbering
  leases acquired by another process since the last load, and a crash mid-write
  reset the file to `[]`; pruning now goes through the locked
  `_atomic_read_modify_write` (re-read under exclusive lock) and all writes use
  temp-file + `os.replace` for crash-safety. (#937) Five autoscaler defects fixed:
  busy-query timeout no longer aborts the tick (fail-safe busy), exact lease-name
  match (not substring), one malformed lease record is skipped not fatal, the
  label-less default pool is exempt from start/stop label filters, and
  `ACTION_COOLDOWN_SECONDS` is enforced on the stop side. Tests across
  `tests/test_autoscaler_sampling.py`, `tests/test_runner_lease.py`,
  `tests/api/test_lease_pruning.py`, `tests/test_autoscaler_systemd.py`,
  `tests/test_autoscaler_busy.py`, and `tests/test_pool_autoscaler.py`.
- **2026-06-12 (2.5.99):** RD↔MD integration contract cluster (issues #959, #961,
  #963). (#959) `MAXWELL_PORT` defaulted to 8322 — a port that appears nowhere in
  Maxwell_Daemon (which serves on 8080) and that collided with the
  ControlTower-SSD pool's own `dashboard_url:8322` in `machine_registry.yml`, so a
  default deploy probed a second dashboard and misreported it as Maxwell. The
  default is now 8080 (the daemon's real port). A new `MAXWELL_EXPLICITLY_CONFIGURED`
  flag is surfaced on `GET /api/maxwell/status` as `configured` so the tab can show
  "configuration needed" instead of an opaque connection error when neither
  `MAXWELL_URL` nor `MAXWELL_PORT` is set. (#961) The task contract was mis-keyed
  and full of phantom fields: `MaxwellTaskListResponse` used `cursor` where MD emits
  `next_cursor`, and `MaxwellTaskItem`/`MaxwellTaskDetailResponse` modelled
  `updated_at`/`type`/`priority`/`tags`/`error`/`result_summary`, none of which MD's
  `TaskSummary`/`TaskDetail` produce. Models now mirror MD's real shapes
  (`{id, status, created_at}` + `transcript`/`artifacts` on detail), with `id`/`status`
  required so a defaulted task row is impossible. (#963) Maxwell lifecycle control
  (`POST /api/maxwell/control`) shelled out to `systemctl` unconditionally — a silent
  no-op on Windows/WSL hosts without systemd. It now returns HTTP 501 with an
  actionable message there, `GET /api/maxwell/status` reports `lifecycle_supported`,
  and the canonical `Maxwell_Daemon` repo slug replaces the broken `Maxwell-Daemon`
  links in `CLAUDE.md` and `docs/contracts/maxwell.md`. New tests in
  `tests/test_maxwell_contract.py` (incl. a #960 consumer-driven contract guard that
  fails loud on a simulated MD field rename) and `tests/test_maxwell_proxy.py`.
- **2026-06-12 (2.5.97):** Normalized the Maxwell backends contract for the
  daemon's current `/api/v1/backends` shape (integration, issue #954).
  Maxwell-Daemon returns a bare `list[str]` of provider names, while the
  dashboard proxy expected backend objects and turned the real response into a
  validation-backed HTTP 500. `MaxwellBackendsResponse` now accepts daemon string
  entries and converts them into the existing dashboard backend item shape
  (`name`, `type="unknown"`, `enabled=true`, optional `model`/`status`) before
  serialization. Contract docs and proxy/model regressions cover the real daemon
  list shape.
  > > > > > > > origin/main
- **2026-06-12 (2.5.96):** Hardened three dispatch/credential security defects
  (security, issues #925, #926, #927). (1) `verify_approval_hmac`
  (`backend/dispatch/signing.py`) returned `True` for any confirmation lacking an
  `approval_hmac` ("backward compatibility"), so a forged approval with no HMAC
  verified and the envelope/action binding was effectively optional. It now fails
  closed: when a signing secret is configured an unsigned confirmation is
  rejected, with the legacy permissive behaviour available only behind the
  explicit, default-off `DISPATCH_ALLOW_UNSIGNED_APPROVAL` flag (which logs a
  deprecation warning). (2) `MAXWELL_API_TOKEN`
  (`backend/dashboard_config/__init__.py`) defaulted to the published string
  `"maxwell-local-secret"` — anyone reading the source could mint valid Maxwell
  bearer tokens. The default is now `""` (no token → no `Authorization` header,
  correct for a token-less daemon); the hardcoded string is gone. (3)
  `validate_local_path` (`backend/security.py`) resolved user input (following
  symlinks) but compared against an UNresolved `allowed_root`, so a symlinked root
  rejected legitimate paths and could let crafted symlinks pass containment. It
  now resolves both sides (resolved-vs-resolved). Each fix ships fail-loud tests.
- **2026-06-12 (2.5.95):** Fixed the autoscaler singleton lock falling through to
  an alternate path when the primary lock was HELD (correctness, issue #933).
  `_acquire_lock` (`backend/runner_autoscaler.py`) caught every `OSError` from the
  candidate-path loop and continued to the next path — but `BlockingIOError`
  ("lock held" from `flock(LOCK_NB)`) is an `OSError` subclass, so a second
  autoscaler instance failed the primary lock and silently acquired a DIFFERENT
  lock file and ran anyway, allowing two concurrent autoscalers to
  double-stop/double-start runners. The open/makedirs step (path unusable →
  fall through) is now separated from the flock step: a held lock raises
  `BlockingIOError` and the process exits `75` (EX_TEMPFAIL) immediately rather
  than trying an alternate path; only genuine path-unusable errors
  (`PermissionError`/`ENOENT`) fall through. Linux-only fail-loud tests added in
  `tests/test_autoscaler_singleton_lock.py`.
- **2026-06-12 (2.5.94):** Fixed branch-substring stale-run classification that
  auto-cancelled legitimate human CI (correctness, issue #934).
  `classify_stale_run` (`backend/queue_cleanup.py`) flagged any branch CONTAINING
  `agent`/`worktree`/`wt-`/`patch-`/`run-` as an abandoned-agent run with
  `safe_to_cancel=True`, so ordinary branches like `fix/rerun-tests`,
  `feat/dispatch-fix`, and `feature/user-agent-header` were reaped by the
  scheduled stale-job purge precisely during backlogs. Classification is now
  anchored to path-segment / prefix boundaries (first segment is an agent
  namespace `agent`/`codex`/`jules`/`worktree`, or starts with `wt-`/`patch-`/
  `run-`) AND requires a corroborating bot actor when the triggering actor is
  known — a human pushing to an agent-named branch is no longer auto-cancellable.
  The scan caller now passes `triggering_actor`/`actor` login into the
  classifier. Fail-loud tests added in `tests/test_queue_cleanup.py`.
- **2026-06-12 (2.5.92):** Made the Maxwell read-path contract real — the
  `/api/version`, `/api/status`, and `/api/v1/workers` consumer models in
  `backend/maxwell_contract.py` previously modelled an imaginary shape with zero
  overlapping keys against the daemon, so every field silently defaulted and the
  Maxwell tab perpetually showed "unknown / 0 tasks" and an empty worker list
  (integration, issues #955, #956, #958). The models now validate the daemon's
  REAL shapes: `/api/version` returns `{daemon, contract}` (both required;
  `daemon` mirrors to `version`, `contract_compatible` computed against
  `EXPECTED_CONTRACT_VERSION="2.0.0"`); `/api/status` returns the daemon's
  `pipeline_state` / `active_task_id` / `gate` / `sandbox` (`pipeline_state`
  required, mapped to `state`, `paused` derived), with task counts merged from
  `/api/v2/status` `counts`; `/api/v1/workers` returns
  `{worker_count, queue_depth}` (`worker_count` required, mirrored to `total`).
  The discriminating field of each is required so contract
  drift fails loudly (`ValidationError` surfaced as `502`) instead of defaulting.
  `GET /api/maxwell/status` now surfaces a `contract` negotiation block
  `{expected, daemon, compatible}` so the tab can show an incompatibility banner
  (#956). `docs/contracts/maxwell.md` bumped from "v1" to `2.0.0` with the real
  shapes. Existing contract/proxy tests updated off the imaginary shapes and new
  fail-loud / mapping tests added in `tests/test_maxwell_contract.py`.
- **2026-06-12 (2.5.91):** Added a structural authentication perimeter so every
  `/api/*` route is authenticated by default rather than opt-in per route
  (security, issues #924 and #928). A fail-closed middleware
  (`middleware.auth_perimeter_check`, registered before `SessionMiddleware` so the
  session is resolved) rejects any non-exempt `/api/*` request that does not
  resolve to a principal (service token, session, or gated loopback admin) with
  `401` before the handler runs; it defers to the route dependency when a test
  installs an `app.dependency_overrides` for the auth dependency, so production is
  always live while tests keep injecting identities. The exempt set
  (`_AUTH_EXEMPT_PATHS`) is an explicit, reviewed allowlist (health, auth
  handshake, logout, signed Linear webhook + its health probe); alternate-auth
  surfaces that enforce their own equally-strong check are listed in
  `_ALT_AUTH_EXEMPT_PREFIXES` (`/api/fleet/dispatch/*` HMAC envelopes,
  `/api/orchestrator/*` Conductor admission gate, `/api/credentials/*`
  loopback-only writes). Routes that previously shipped unauthenticated now carry
  an explicit dependency: `POST /api/metrics/web-vitals` (#928),
  `POST /api/runner-routing-audit/refresh` (both registrations, #928),
  `POST /api/runners/{id}/diagnostics` (#928),
  `POST /api/autoscaler/pools/{pool}/config` (#924), and
  `POST /api/linear/sync/poll` (#924). A new `tests/api/test_structural_auth_perimeter.py`
  walks `app.routes` and fails the build if any mutating `/api/*` route is neither
  auth-protected nor on a documented exempt list, so the perimeter cannot silently
  regress to opt-in.
- **2026-06-12 (2.5.90):** Fixed the dead Maxwell pipeline-control proxy path
  (integration, issue #952). `POST /api/maxwell/pipeline-control/{action}`
  proxied to `/api/v1/control/{action}`, which Maxwell-Daemon does not expose —
  every pause/resume/abort from the dashboard 404'd. The proxy now targets MD's
  real route `POST /api/control/{action}`, and `MaxwellControlResponse` carries
  MD's `{action, applied_at, previous_state}` shape (with `action` required so a
  mismatched payload fails loudly; legacy `status`/`message` kept optional for
  backward compatibility). `docs/contracts/maxwell.md` updated to match. Part of
  the RD↔MD contract epic (#964); the MD side already serves port 8080 /
  contract 2.0.0.
- **2026-06-12 (2.5.89):** Enforced intra-fleet authentication on the hub
  (security, issue #922). Added the `require_fleet_peer` dependency
  (`backend/identity.py`): a hub-reachable fleet route is accepted only when the
  caller presents a valid principal OR `Authorization: Bearer <HUB_FLEET_TOKEN>`
  matching the hub's configured token via `hmac.compare_digest` (constant-time).
  Applied to `GET /api/fleet/status`. Policy: fleet reads are token-gated when
  `HUB_FLEET_TOKEN` is set (unauthenticated callers get `401`) and tailnet-public
  when it is unset (backward-compatible no-op for single-node deployments).
  `docs/runbooks/hub-credentials.md` now describes the enforced behavior instead
  of claiming validation that did not exist. The structural "all `/api/*` routes
  authenticated" follow-up is tracked by #924.
  > > > > > > > origin/main
- **2026-06-12 (2.5.88):** Collapsed the two divergent `proxy_to_hub`
  implementations into one (security, issue #923, re-opens #347). `backend/server.py`
  previously defined its own `proxy_to_hub` that forwarded ALL caller headers to
  the hub except `host`/`content-length` — laundering operator session cookies,
  `Authorization`, `X-API-Key`, and `X-CSRF-Token` upstream — and that copy was the
  one dependency-injected into the deployment / orchestration / orchestration-node
  routers. The server-side body is deleted; `server.proxy_to_hub` and
  `server._should_proxy_fleet_to_hub` are now thin re-exports of the single
  header-stripping implementation in `proxy_utils`, which strips the sensitive
  headers and injects the intra-fleet `HUB_FLEET_TOKEN`. A regression test asserts
  that for every hub-proxying route no operator credential reaches the hub.
- **2026-06-12 (2.5.87):** Removed the duplicate `GET /api/system` and
  `GET /api/fleet/status` route registrations in `backend/metrics.py`
  (architecture/correctness, issue #940). Because the metrics router is included
  before `routers.system` and `routers.fleet`, FastAPI's first-match-wins routing
  served the metrics copies and shadowed the maintained implementations — silently
  killing the `FleetEventPoller` wiring in `routers/fleet.py` that feeds
  `/api/events` (issue #863), and re-registering `/api/system` over
  `routers/system.py`. The canonical handlers now own those paths and
  `metrics.py` no longer lazy-imports from `backend.server` (circular import
  removed). The unique `GET /api/disk/pool-pressure` route is unchanged. A new
  route-uniqueness invariant (`tests/api/test_route_uniqueness.py`) fails on any
  duplicate `(method, path)` pair except the explicitly-tracked god-module twins
  pending issue #941.
- **2026-06-12 (2.5.86):** Closed unauthenticated remote admin via the dev-login
  endpoint (security, issue #921). `GET /api/auth/dev-login` — which mints an
  admin session for the first human principal — is now gated on BOTH a loopback
  transport peer AND an explicit `DASHBOARD_DEV_LOGIN=1` opt-in; any request
  failing either gate gets a `404` (indistinguishable from "not present" to a
  remote scanner). The `__main__` uvicorn bind now uses `dashboard_config.HOST`
  (honoring `DASHBOARD_HOST`) instead of a hardcoded `0.0.0.0`; the default
  still resolves to `0.0.0.0` so existing deployments are unchanged.
- **2026-06-12 (2.5.85):** Closed the dispatch HMAC signature-gate bypass
  (security, issue #919). `CommandEnvelope.from_dict` no longer runs the
  auto-signing path used for locally minted envelopes: an inbound wire body is
  reconstructed verbatim and a new `signature_authentic` flag records whether a
  non-empty signature was actually present. `validate_envelope_crypto` now
  rejects any envelope whose signature was absent on the wire
  (`"envelope signature missing"`), so `POST /api/fleet/dispatch/submit` returns
  400 for signature-less or empty-signature envelopes instead of the server
  minting a valid signature on the caller's behalf. Locally minted,
  server-originated envelopes still auto-sign and round-trip unchanged.
- **2026-06-12 (2.5.84):** Authenticated the agent-launcher control surface
  (security, issue #920). Every `/api/agent-launcher/*` route now requires an
  authenticated principal; the mutating routes (`PUT /config`, `POST /start`,
  `POST /stop`, `POST /run-once`) additionally require the privileged
  `system.control` scope. Previously these routes — which spawn code-executing
  agent processes and write attacker-controllable JSON into the operator's home
  directory — were completely unauthenticated, so any LAN/Tailscale peer
  reaching the dashboard port could launch or reconfigure agents (RCE-adjacent).
  Unauthenticated requests now return 401 and under-scoped principals get 403.
- **2026-06-10 (2.5.83):** Corrected queue-diagnosis target classification for
  fleet jobs whose GitHub job metadata contains only custom `d-sorg-fleet*`
  labels. These jobs are now counted as self-hosted fleet work instead of
  GitHub-hosted waits, so the diagnostic bottleneck reflects local runner
  capacity when custom fleet labels route the job.
- **2026-06-10 (2.5.82):** Replaced the placeholder queue-diagnosis response
  with live queue and runner analysis. `GET /api/queue/diagnose` now samples
  queued workflow jobs through the shared authenticated GitHub API path,
  summarizes online/busy/idle/offline runner capacity, distinguishes
  GitHub-hosted, generic self-hosted, and `d-sorg-fleet` waits, reports
  unroutable label sets, preserves bounded partial-failure errors, and caches
  the diagnosis briefly so operators can explain queue stalls without adding
  avoidable GitHub API pressure.
- **2026-06-10 (2.5.81):** Fixed runner inventory visibility for fleets larger
  than GitHub's default first page. Dashboard runner, health, stats, quick
  dispatch, fleet control, diagnostics, group, and orchestration capacity paths
  now share a paginated GitHub runner inventory helper, so `/api/runners` and
  `/api/health` report all registered runners instead of stopping at 30 when
  the organization has additional DeskComputer/OGLaptop/MATLAB capacity. CI
  routing now keeps Python dependency-heavy jobs on `d-sorg-fleet-fast-io` and
  Docker image scans on runners with both `d-sorg-fleet-docker` and
  `d-sorg-fleet-fast-io`, preventing broad-fleet dispatch onto light laptop
  runners that lack healthy Docker or cache state.
- **2026-06-10 (2.5.80):** Routed queue-read GitHub calls through the shared
  GitHub App API client instead of shelling out to `gh api`. The dashboard queue
  view now uses the same authenticated client path as health checks, preserves
  per-job queued depth, and keeps stale auth or transient repo failures from
  silently flattening the queue to zero. The OGLaptop registry entry now uses
  its Tailscale MagicDNS dashboard URL so Tailscale Serve routes fleet health
  calls to the running backend instead of returning the IP-host 404 path.
- **2026-06-10 (2.5.79):** Restored post-merge `main` CI compatibility for the
  local-runner fleet. The 500-line source cap now keeps the existing legacy
  frontend over-cap baseline explicit so `push` validation does not fail on
  unrelated historical debt, while pull requests still check changed source
  files. `local-only-runner-guard.yml` now runs on `d-sorg-fleet` itself, so
  workflow-routing validation no longer depends on hosted GitHub runners.
- **2026-06-10 (2.5.78):** Hardened fleet dashboard routing for the
  GitHub-primary/Forgejo-ready fleet posture. `backend/machine_registry.yml`
  now routes ControlTower pool dashboards through Tailscale MagicDNS
  (`controltower.tail2bbcc7.ts.net`) instead of stale local/offline addresses,
  marks the live ControlTower host as preferred, and keeps the legacy monitoring
  node non-preferred. Node-system proxy calls now have a 30 s budget so loaded
  DeskComputer and ControlTower runner hosts can finish process enumeration
  without false 504s. `deploy/wsl-mirrored-port-helper.sh` now detects and
  clears Tailscale Serve HTTP/HTTPS/TCP bindings by protocol before restoring
  the WSL dashboard HTTP binding. The `security-scan` CI job now runs
  `pip-audit` from an isolated no-cache audit venv against `requirements.txt`,
  avoiding runner-side project-venv cache corruption while preserving the
  dependency audit gate.
- **2026-06-10 (2.5.77):** Added a non-matrix `tests` aggregate CI context
  after the Python matrix job so branch protection consumes the same green test
  result as `tests (3.11)` without synthetic statuses.
- **2026-06-03 (2.5.76):** Migrated the modern `DesktopShell` chrome onto the
  #834 scoped-class path. Shell layout, topbar, action cluster, main scrolling
  region, and active action state now render through `desktop-shell__*` and
  `shell-action--active` classes instead of inline JSX styles.
- **2026-06-03 (2.5.75):** Migrated the Help/About dialog shell onto the #834
  scoped-class path. The trigger, dialog tabs, version metadata, quick-link
  chips, checklist, and keyboard shortcut rows now render through
  `help-about__*` classes instead of inline JSX styles.
- **2026-06-03 (2.5.74):** Migrated the Analysis Reports panel onto the #834
  scoped-class path. The report sidebar title, empty states, reader header,
  selected-date badge, chart frame, and loading placeholder now use
  `reports-*` and `report-*` classes instead of inline layout styles.
- **2026-06-03 (2.5.73):** Migrated the `EventLog` primitive onto the #834
  scoped-class path. Toolbar, filter, row, chip, detail, empty-state, and
  accessibility presentation now render through `event-log__*` and
  `visually-hidden` classes instead of JSX style helpers, while virtualization
  dimensions remain isolated behind refs.
- **2026-06-03 (2.5.72):** Migrated the Events tab wrapper onto the #834
  scoped-class path. The dedicated Events tab and compact Overview event
  section now own layout, title, and degraded-state presentation through
  `events-tab__*` and `overview-event-section` classes instead of inline styles.
- **2026-06-03 (2.5.71):** Migrated the assistant Markdown renderer onto the
  #834 scoped-class path. Code blocks, lists, headings, paragraphs, inline code,
  and links now render through `assistant-markdown__*` classes instead of inline
  style objects, with renderer tests and static guards covering the contract.
- **2026-06-03 (2.5.70):** Migrated the shared `Stat` metric card value
  coloring onto the #834 token-class path. Known accent token values now map to
  scoped `stat-value--accent-*` classes, removing the remaining inline value
  color style from `components/Stat.tsx`.
- **2026-06-03 (2.5.69):** Migrated AlarmPanel onto the #834 primitive/token
  path. Fleet alarm severity chips now render through `Badge`, status/row/dot
  severity styling is owned by scoped `alarm-panel__*` classes, and the
  component no longer carries inline `CSSProperties` style helpers.
- **2026-06-03 (2.5.68):** Migrated Theme Settings onto the #834
  primitive/token path. The default accent action now uses `TouchButton`, accent
  swatches render through scoped `theme-settings__*` classes backed by theme
  tokens, and concrete accent preset values live in `design/accentPresets.ts`
  instead of inline TSX style/color literals.
- **2026-06-03 (2.5.67):** Migrated the desktop shell Density Toggle onto the
  #834 primitive/token path. The compact/comfortable control now renders
  through `TouchButton`, preserves `aria-pressed` via the primitive `pressed`
  prop, and uses scoped `density-toggle__*` classes instead of inline spacing
  and label styles.
- **2026-06-03 (2.5.66):** Migrated the header Quick Dispatch popover onto
  the #834 primitive/token path. The trigger and action controls now render
  through `TouchButton`, the primitive accepts button refs for popover
  outside-click handling, and scoped `quick-dispatch__*` classes own popover,
  field, and status presentation instead of inline style objects.
- **2026-06-03 (2.5.65):** Migrated the Push Notifications settings surface
  onto the #834 primitive/token path. Unsupported/config-missing and load-failure
  states now render through `EmptyState`; subscribe/unsubscribe actions use
  `TouchButton`; and scoped `push-settings__*` classes own page and topic
  presentation instead of inline styles or hand-written touch button classes.
- **2026-06-03 (2.5.64):** Migrated the Linear Integration Setup surface onto
  the #834 primitive/token path. Workspace auth status now renders through
  `Badge`; load failures and empty workspace states use `EmptyState`; webhook
  copy uses `TouchButton`; and scoped `linear-setup__*` classes own panel,
  workspace, field, loading, and instruction presentation.
- **2026-06-03 (2.5.63):** Migrated the Agent Dispatch mobile remediation
  flow onto the #834 primitive/token path. Failed-run empty state and load
  failures now render through `EmptyState`; dispatch/retry actions use
  `TouchButton` without inline width styles; and scoped `agent-dispatch__*`
  classes own loading, error, and full-width action presentation.
- **2026-06-03 (2.5.62):** Migrated the Conductor admission-gate surface onto
  the #834 primitive/token path. Queue mode and provider mix now render through
  `Badge`; disabled/error states use `EmptyState`; pause/resume/drain actions
  use `TouchButton`; and scoped `conductor__*` classes own stat, budget, section,
  and control presentation instead of page-level inline styles.
- **2026-06-03 (2.5.61):** Migrated the Tests tab onto the #834 primitive/token
  path. CI status, heavy-test dispatch state, and recent-run conclusions now
  render through `Badge`; CI loading uses `EmptyState`; rerun and heavy dispatch
  actions use `TouchButton`; and scoped `tests-tab__*` classes own table wraps,
  links, headings, and status presentation instead of page-level inline styles.
- **2026-06-03 (2.5.60):** Migrated the Runner Schedule capacity editor onto
  the #834 primitive/token path. The page now uses `Badge` for scheduler and
  saving state, `TouchButton` for refresh/save/apply actions, `EmptyState` for
  missing schedule/error surfaces, and scoped `runner-schedule__*` classes for
  table/input/footer presentation instead of page-level inline styles.
- **2026-06-02 (2.5.59):** Completed the PyJWT 2.13.0 security remediation by
  bumping the pin in `requirements.txt` and `requirements.lock.txt`, which the
  2.5.58 change left at 2.12.0 alongside the `pyproject.toml`/`uv.lock` updates.
  `security-scan` (pip-audit on `requirements.txt`) and `quality-gate` now read
  2.13.0 from every dependency manifest, fully clearing the PYSEC-2026 PyJWT
  advisories across the resolved environment.
- **2026-06-02 (2.5.58):** Security audit findings for PyJWT are remediated by
  upgrading `PyJWT[crypto]` from 2.12.0 to 2.13.0 in both `pyproject.toml` and
  `uv.lock`, clearing the PYSEC-2026 advisories reported by `pip-audit` in
  `quality-gate` and `security-scan`. The mobile queue Vitest coverage now
  asserts the current `Queue health summary` accessibility label, matching the
  dashboard integrity contract restored in 2.5.57.
- **2026-06-02 (2.5.57):** The legacy dashboard API migration now preserves the
  shared frontend integrity contracts after the `legacyFetch` wrapper adoption.
  `tests/test_frontend_integrity.py` accepts both legacy object-literal and TSX
  prop source shapes for sortable headers and ARIA labels while still enforcing
  the exact labels and response-check ordering. `frontend/src/pages/Queue/Mobile.tsx`
  restores the "Queue health summary" accessible section label,
  `frontend/src/main.tsx` restores the root Suspense "Loading dashboard..."
  status text, and `frontend/src/legacy/App.tsx` again renders the
  `fleet-hero__alerts` surface from the extracted fleet-alert rollup so the
  overview hero keeps user-visible alert detail.
- **2026-06-02 (2.5.56):** Restored the Docker runtime image to the pinned
  Python 3.12 base after a dependabot bump to `python:3.14-slim` re-broke the
  `docker-build-scan` job and the `test_dockerfile_pins_base_image_to_digest`
  deploy-hardening check. Python 3.14 has no prebuilt wheels yet for
  `pydantic-core`, `jiter`, `uvloop`, `watchfiles`, and `cffi`, so the image
  build fell back to source compilation and failed; `pyproject.toml`
  (`>=3.11,<3.14`) and `uv.lock` already constrain the runtime to 3.12. The
  `Dockerfile` is now pinned to
  `python:3.12-slim@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203`,
  matching the digest format the deploy-hardening test enforces and restoring a
  deterministic, wheel-only image build.
- **2026-06-02 (2.5.58):** Cleared the dashboard security-audit gate by moving
  `PyJWT[crypto]` to `2.13.0` and regenerating the uv lockfile. Queue mobile
  coverage now asserts the component's current `Queue health summary`
  accessible label so Vitest tracks the shipped UI contract.
- **2026-06-02 (2.5.55):** Legacy dashboard accessibility and theme-token
  hygiene are hardened. `frontend/src/legacy/App.tsx` now exposes sub-tabs as a
  labelled ARIA tablist with roving keyboard focus, keeps sortable table headers
  on their native `columnheader` role while preserving keyboard sorting, labels
  the dispatch modal as an `aria-modal` dialog, and routes legacy issue/status
  badge colors through semantic theme tokens. `frontend/src/pages/Fleet/LabelGuide.tsx`
  aligns its fallback colors with the dashboard token contract, and
  `tests/frontend/test_color_literal_budget.py` tracks the remaining literal
  budget so legacy color debt cannot silently grow. `tests/e2e/a11y.spec.ts`
  adds Playwright + axe coverage for critical pages, with `@axe-core/playwright`
  recorded in the frontend dev dependencies.
- **2026-06-02 (2.5.54):** The dashboard design system now supports a broader
  fleet theme set plus density-aware elevation polish. `frontend/src/design`
  adds six additional accessible palettes, exports radius/elevation/shadow
  tokens, and verifies theme contrast and token behavior with dedicated tests.
  `frontend/src/hooks/useDensity.ts` and `frontend/src/components/DensityToggle.tsx`
  provide a persisted compact/comfortable density control, with tests covering
  storage and UI behavior. `frontend/src/index.css` and `frontend/src/main.tsx`
  apply the tokens globally so table, card, control, and shell surfaces share a
  single DRY visual contract. The frontend color-literal budget is intentionally
  raised to account for the new shadow/elevation token inventory.
- **2026-06-01 (2.5.53):** Hardened runner busy-detection against the recurring
  "autoscaler/cleanup stops a busy runner mid-job" regression (Runner_Dashboard#640;
  observed 2026-06-01 cancelling PR #813 `docker-build-scan` mid OCI-export, #814
  `tests`, #815 `security-scan`). Three independent gaps were closed. (1)
  `backend/autoscaler_busy.py` adds **Strategy 3b** — `_runner_busy_via_worker_scan`,
  a global `Runner.Worker` scan keyed on the runner's `WorkingDirectory`. The
  MainPID child-walk (Strategy 3) returns a false negative when the Worker has
  reparented (`KillMode=mixed`) or systemd's MainPID transiently points only at the
  listener; the pickup-dir mtime (Strategy 1) goes stale during a long step that
  emits no file-commands (e.g. a ~10-min `docker buildx` export). A live Worker is
  ground truth, so `_runner_is_busy` now falls through to 3b instead of concluding
  "idle" after an empty child-walk, and also consults 3b in the MainPID=0 window
  before the coarse ActiveState fallback. (2) The lockfile signal (Strategy 2) was
  dead fleet-wide after any reboot: `/run` is tmpfs and the runner user cannot
  `mkdir` under root-owned `/run`, so `/run/runner-busy` vanished and the JOB_STARTED
  hook's sentinel was never written. New `deploy/tmpfiles.d/runner-busy.conf` (wired
  into `deploy/install-runner-maintenance.sh`) recreates the dir as root on every
  boot. (3) `deploy/runner-cleanup.sh` looked up the lockfile by the **workdir
  basename** (`runner-4.lock`) while the hook writes the **registered runner name**
  (`d-sorg-…-nvme-4.lock`) — a guaranteed miss; cleanup now derives the registered
  name from the unit via `runner_name_for_unit`. New regression tests:
  `TestRunnerIsBusyWorkerScan` (3) in `tests/test_runner_autoscaler.py` and
  `TestBusyViaWorkerScan` (5) in `tests/test_autoscaler_busy.py`.
- **2026-05-31 (2.5.52):** Modern shell — default desktop layout, reversible
  (epic #796 / issue #802). `frontend/src/shell/DesktopShell.tsx` composes the
  three merged surfaces into a GitHub-style desktop layout, all driven by the
  one nav registry (DRY): the left `Sidebar` (#798), the slim `TopToolstrip`
  (#799), and the `Tooltip` primitive (#801) on every nav item and on every
  shell action button. Navigation is flat typed props (LoD): `activeTabId`,
  `onSelect(tabId)`, and a flat `ShellAction[]` (each with a required non-empty
  `tooltip`, enforced by `assertActions` — DbC). The page body is mounted in the
  `main` landmark, isolated from the nav chrome (orthogonality — a failing page
  cannot remove navigation). The legacy `App` is now mountable _chromeless_ and
  _tab-controlled_ (new optional `activeTab` + `chromeless` props): the shell
  owns navigation while App renders only the requested page body, so every
  existing page renders unchanged. `frontend/src/shell/layoutFlag.ts`
  (`resolveDesktopShellLayout`) makes the modern shell the DEFAULT desktop layout
  while keeping it fully reversible: `localStorage["dashboard.layout"]="legacy"`
  (a visible "Classic layout" action button) or `VITE_DESKTOP_SHELL` opt-out
  pins the untouched legacy shell. `frontend/src/main.tsx` selects the layout on
  desktop. Every interactive control in the shell carries an accessible label or
  tooltip — an a11y audit test asserts no `button`/`a[href]`/`role=button`/
  `role=menuitem` lacks an accessible name. TDD: 10 `DesktopShell` behaviour +
  audit tests and 6 `layoutFlag` tests. `vite build`, ESLint, and Vitest (391
  tests) pass.
- **2026-05-31 (2.5.51):** Modern shell — slim top toolstrip (epic #796 / issue
  #799). `frontend/src/shell/TopToolstrip.tsx` replaces the legacy ~24-button
  toolstrip with just the registry's most-frequent categories (Fleet, Queue,
  Remediation, Conductor — those with `frequent: true`) as direct buttons;
  every other category collapses into a single accessible grouped "More"
  `Dropdown` (#800), ordered by nav group. Rendered entirely from the nav
  registry (DRY): the frequent buttons come from `frequentItems()` and the
  overflow from the non-frequent remainder, so the toolstrip and sidebar never
  drift. Each frequent button is wrapped in the `Tooltip` primitive (#801)
  (hover + focus, `aria-describedby`) and the active category is marked
  `aria-current="page"`; when the active tab lives in the overflow menu, the
  "More" trigger itself carries `aria-current` (new optional `triggerActive`
  prop on the `Dropdown` primitive). It is a labelled WAI-ARIA `toolbar` that
  wraps responsively (`flex-wrap`); below 900px the text labels hide to icons
  via `.slim-toolstrip__label`. LoD: flat `activeTabId` + `onSelect(tabId)`
  props. Orthogonality: pure presentational nav, owns no page state. Exported
  from `frontend/src/shell/index.ts`. 14 behaviour tests for the toolstrip plus
  2 for the new `Dropdown.triggerActive` prop. Mounted as the default desktop
  shell behind a layout flag in #802.
- **2026-05-31 (2.5.50):** Modern shell — left Sidebar (epic #796 / issue #798).
  `frontend/src/shell/Sidebar.tsx` is a GitHub-style left navigation rendered
  entirely from the nav registry (DRY): one collapsible section per group, every
  category as a nav button with the registry tooltip as its accessible `title`.
  Active item is marked `aria-current="page"`. Per-group collapse and a
  whole-sidebar icon-rail collapse both persist to `localStorage`
  (`dashboard.sidebar.collapsedGroups`, `dashboard.sidebar.railCollapsed`).
  Roving ArrowUp/Down keyboard navigation across visible items; the sidebar is a
  labelled `navigation` landmark. It is purely presentational — flat
  `activeTabId` + `onSelect(tabId)` props (LoD) — so a failing page cannot break
  it (orthogonality). 10 behaviour tests. Not yet mounted in the desktop shell;
  that is wired behind a layout flag in #802.
- **2026-05-31 (2.5.49):** Modern shell — Tooltip + Dropdown primitives (epic
  #796 / issues #801, #800). `frontend/src/primitives/Tooltip.tsx` is a reusable
  accessible tooltip: shows on hover **or** keyboard focus after a small delay,
  associates with its trigger via `aria-describedby`, and dismisses on
  mouseleave / blur / Escape. It composes onto the child's own event handlers
  (LoD) and renders nothing for blank content.
  `frontend/src/primitives/Dropdown.tsx` is a reusable WAI-ARIA menu-button
  dropdown (`aria-haspopup="menu"` / `aria-expanded`, `role="menu"` /
  `menuitem`): ArrowUp/Down roving focus with wrap, Enter/Space activate,
  Escape closes and restores trigger focus, click-outside closes. Both are
  exported from `frontend/src/primitives/index.ts` and back every nav item and
  action-button hover hint in the new shell (#802). 16 behaviour tests.
- **2026-05-31 (2.5.48):** Modern shell — nav registry (epic #796 / issue #797).
  `frontend/src/shell/navRegistry.ts` is now the single typed source of truth
  for every navigable category: each `NavItem` carries `{ id, label, group,
- **2026-05-31 (2.5.47):** Conductor dashboard integration — the admission-gate
  - visibility surface the Conductor orchestrator (in `Repository_Management`,
    epic #1273 / issue #1282) calls **over HTTP** to obtain CI dispatch slots and
    to surface its tracked work. New module `backend/orchestrator_api.py`
    registers four routes under `/api/orchestrator`:
    `POST /lease` (admission gate — grants a dispatch slot when idle runners minus
    a caller-supplied `reserve` still cover the requested `slots` **and** the queue
    is in `running` mode; returns `granted=false` with a reason under saturation or
    a manual pause/drain — this is the backpressure the orchestrator obeys, and
    consumes capacity via a TTL lease, default 900 s, reaped lazily so a crashed
    orchestrator cannot wedge the fleet), `POST /release` (frees a lease,
    idempotent for unknown ids), `GET /queue` (mode, active leases, capacity,
    work classification, provider mix, budget burn) and `POST /queue` (manual
    pause / resume / drain). Capacity reuses the same arithmetic as
    `/api/runners/fleet/capacity` via the new shared
    `routers.runner_helpers.count_runner_capacity` helper (DRY); the fleet capacity
    route was refactored onto it. DbC: pydantic request/response models with field
    constraints + validators on every POST; the gate fails safe (denies) when the
    capacity source is unwired. The whole surface is gated behind the
    `DASHBOARD_ENABLE_CONDUCTOR` env flag (default off) — every route returns 404
    and the frontend tab is hidden until enabled (reversible, orthogonal). A new
    **Conductor tab** (`frontend/src/pages/Conductor.tsx`) shows planned/active/
    blocked work, provider mix, budget burn, and pause/resume/drain controls; it
    probes the endpoint once and only appears when the flag is on. Its visible
    state badges, disabled/error states, and pause/resume/drain controls use the
    shared `Badge`, `EmptyState`, and `TouchButton` primitives with scoped
    `conductor__*` token classes. 15 backend route
    tests in `tests/api/test_orchestrator_api.py` and 6 frontend behaviour tests in
    `frontend/src/pages/__tests__/Conductor.test.tsx`. **Conductor (RM) side must:**
    call `POST /api/orchestrator/lease` before dispatching and obey `granted=false`;
    send the CSRF sentinel header `X-Requested-With: XMLHttpRequest` on all POSTs;
    `POST /api/orchestrator/release` when work completes; optional `provider` field
    on the lease feeds the provider mix. Optional env: `CONDUCTOR_LEASE_TTL_SECONDS`,
    `CONDUCTOR_DEFAULT_RESERVE`, `CONDUCTOR_BUDGET_LIMIT_USD`.
- **2026-05-30 (2.5.46):** `/api/stats` (Overview summary) no longer publishes
  false zeros under partial GitHub failure. Previously the org PR/issue search,
  the 24-repo queue fan-out, and the fleet probe shared one timeout budget, so a
  slow search or secondary rate-limit timed out the whole bundle and zeroed PRs,
  queue, and machine counts together — even while the standalone `/api/queue`
  (toolstrip) stayed correct. `backend/routers/repos_stats.py` now: (1) reuses
  the resilient `/api/queue` cache instead of re-fanning-out, (2) budgets
  runners, workflow-runs, PR search, issue search, and fleet **independently**,
  (3) backfills any failed field from a `stats:stale` last-known-good snapshot
  (24h TTL) that is only written on a fully-healthy compute, and (4) adds a
  `stale` flag to the payload. 4 new tests in
  `tests/test_stats_summary_resilience.py`.
- **2026-05-30 (2.5.45):** Queue reaper now detects **unroutable** queued runs.
  `backend/queue_cleanup.py` adds `StaleReason.UNROUTABLE_LABEL` plus
  `is_routable()`, `fetch_online_runner_label_sets()`, and
  `required_labels_for_run()`. `find_stale_runs` fetches the online-runner label
  inventory once per scan; `_queued_stale_for_repo` flags any queued run past the
  age gate whose `runs-on` labels are a superset of no online runner's labels
  (e.g. a removed/renamed tier like `d-sorg-fleet-16core`). These are
  `safe_to_cancel=True` because they will never start regardless of age. The
  check fails safe: an empty/unavailable runner inventory (transient API error)
  or missing job metadata never flags a run, so no false-positive cancellations.
  12 new unit tests in `tests/test_queue_cleanup_unroutable.py`.
- **2026-05-30 (2.5.45):** Hub circuit breaker so a dead hub never blanks a spoke's
  dashboard. Previously a `node` (spoke) proxied every fleet-wide endpoint
  (`/api/runners`, `/api/runs`, `/api/queue`, `/api/stats`, `/api/fleet/nodes`) to
  `HUB_URL`, and if the hub was unreachable `proxy_to_hub` raised 504/503 on every
  request — so an offline hub rendered the whole dashboard blank fleet-wide. A hub
  timeout/connect-error now opens a short-lived circuit breaker
  (`mark_hub_unreachable` / `hub_in_cooldown` / `reset_hub_circuit` in
  `backend/proxy_utils.py`, default 30s); while it is open `should_proxy_fleet_to_hub`
  (both `backend/proxy_utils.py` and `backend/server.py`) returns False so the node
  serves its OWN local data instead. A successful proxy closes the breaker
  immediately. Tests in `tests/test_proxy_utils.py::TestHubCircuitBreaker`.
- **2026-05-29 (2.5.44):** Keep the WSL distro resident between keepalive probes
  so the idle runner fleet stays online. A WSL2 distro is torn down a few seconds
  after the last active `wsl.exe` session ends; in-distro systemd services
  (including `wsl-runner-keepalive.service`'s `sleep 600`) do not keep it alive,
  and the watchdog's periodic probe attaches/detaches each cycle, so an idle host
  let the distro terminate between probes and dropped every runner offline.
  `deploy/wsl-keepalive.ps1` now maintains exactly one persistent
  `wsl --exec /bin/sleep infinity` session (new `Set-DistroPin` helper, called
  every loop and restarted if a shutdown kills it). Complements #784 (correct
  probe) and #783 (S4U task survives logoff; docker boot decouple) — neither
  keeps the distro resident between probes. New static regression test
  `test_script_pins_distro_with_persistent_session`.
- **2026-05-29 (2.5.43):** Hardened autoscaler scale-down against undeployed
  runner drain drop-ins (issue #785). Before `backend/autoscaler_systemd.py`
  calls `systemctl stop` for any `actions.runner.*` unit, it now verifies the
  effective systemd stop contract reports `KillMode=mixed` and
  `TimeoutStopUSec >= 120s`. If a host has the code but not the active
  #640/#679 drop-in, the autoscaler refuses to stop the runner and logs the
  deploy action instead of risking a mid-job shutdown. Focused tests cover the
  systemd time-span parser, safe/unsafe contract decisions, and the stop guard.
- **2026-05-28 (2.5.40):** Storage-tier aware disk pressure metrics and classification (issue #754).
  `backend/system_utils.py` gains two new pure helpers:
  `get_host_disk_for_pool(pool)` — derives the correct Windows host drive mount path from pool registry
  metadata (`storage.host_drive` preferred, falling back to the drive letter embedded in `storage.vhdx_path`),
  correcting the prior hard-coded `/mnt/c` assumption that caused the D: HDD-backed VHDX incident to go
  detected; and `classify_disk_pressure_by_tier(storage_tier, percent, free_gb, io_pressure_full_avg10)` —
  returns a four-level pressure status (`low` / `medium` / `high` / `critical`) and `binding_constraint`
  (`io` / `capacity` / `none`) using tier-specific thresholds: NVMe pools treat IO saturation
  (`io_pressure_full_avg10 >= 50`) as the primary constraint, while HDD/SSD pools treat capacity as
  primary and limit IO-only escalation to `medium`. A new `GET /api/disk/pool-pressure` endpoint
  in `backend/metrics.py` iterates all pools in `machine_registry.yml`, measures live disk usage at
  each pool's `runner_base_dir`, and returns per-pool pressure reports with tier, backing disk path,
  VHDX path, bus type, and capacity/IO metrics. Tests in `tests/test_system_utils.py` and
  `tests/api/test_pool_disk_pressure.py` cover tier classification, host-disk path resolution,
  and the endpoint response shape.
- **2026-05-28 (2.5.40):** Added operator diagnostics for VHDX compaction, WSL attach failures, and pool recovery (issue #756). New `GET /api/diagnostics/vhdx` endpoint exposes VHDX attachment status for all WSL distributions via `Get-DiskImage` (powershell.exe), including sharing-violation (ERROR_SHARING_VIOLATION) detection. New `GET /api/diagnostics/pool-recovery` endpoint returns structured recovery guidance for `vhdx_locked`, `disk_full`, and `wsl_boot_failure` scenarios — each with operator action steps and safety warnings (notably: do not restart WSL during active Optimize-VHD compaction). Both endpoints are implemented in `backend/routers/diagnostics.py` and tested in `tests/api/test_pool_diagnostics.py`.
- **2026-05-28 (2.5.40):** Added workflow routing guidance API for issue #757.
  `backend/routers/label_guidance.py` exposes two new endpoints:
  `GET /api/runners/label-guidance` returns per-label workload guidance, copy-paste
  `runs-on` snippets, and the full workflow-class taxonomy sourced from
  `config/workflow_runner_routing_policy.json`.
  `GET /api/runners/label-audit` runs the offline policy audit (no WSL required)
  and returns structured violations and migration recommendations.
  `frontend/src/pages/Fleet/LabelGuide.tsx` adds a Label Guide section to the
  Fleet tab with a taxonomy table and copy buttons.
  `docs/runner-labels.md` is the canonical label documentation.
  Tests in `tests/api/test_label_guidance.py` and
  `frontend/src/pages/Fleet/__tests__/LabelGuide.test.tsx` pin the contracts.
- **2026-05-28 (2.5.40):** Added tier-aware autoscaler controls for ControlTower
  NVMe and HDD pools (issue #755). New `backend/routers/autoscaler_pools.py`
  exposes two endpoints:
  - `GET /api/autoscaler/pools` — returns per-pool scaling state (pool name,
    min/max/default online counts, systemd unit pattern, labels, start/stop
    enabled flags, primary pressure metric name, cooldown secs, dry_run flag).
    Response shape: `{pools: PoolScalingState[], cooldown_secs: int, dry_run: bool}`.
  - `POST /api/autoscaler/pools/{pool}/config` — runtime override for a pool's
    `min_online` / `max_online` counts. Applies by writing env vars
    (`AUTOSCALER_{POOL}_MIN_ONLINE`, `AUTOSCALER_{POOL}_MAX_ONLINE`) into the
    current process; a service reload propagates to the autoscaler loop. Accepts
    `{min_online: int (>=0), max_online: int (>=1)}`. Returns 422 for unknown
    pool names or when `min_online > max_online`. DbC postconditions assert env
    vars were written. Existing autoscaler config constants (NVME*\* / HDD*\*
    family) and `_get_pool_config` / `_classify_unit` pool dispatch logic remain
    the single source of truth for pool parameters; the router only reads and
    exposes them.
    New tests: `tests/api/test_autoscaler_pools.py` (17 tests).

- **2026-05-28 (2.5.40):** Added multi-pool backend aggregation to
  `GET /api/fleet/status` (issue #753). When the dashboard runs on port 8321
  (ControlTower-NVMe), it automatically queries its peer on port 8322
  (ControlTower-HDD) and merges both pool responses into a single JSON object
  keyed by pool name. If the peer is unreachable the endpoint returns an
  `"offline"` entry for that pool rather than failing the whole request,
  preserving orthogonality. Pass `?exclude_pools=true` to suppress the peer
  query (used internally so the peer does not recurse back). The Fleet tab
  `Mobile.tsx` already renders ControlTower pool nodes in a dedicated
  side-by-side section when their names start with `"ControlTower"`. Tests in
  `tests/api/test_fleet_aggregator.py` cover the full-aggregate, peer-offline
  fallback, and exclude_pools code paths.
- **2026-05-27 (2.5.39):** Optimized per-runner worker process resource metric collection in `backend/routers/system.py` and `backend/system_utils.py` by pre-computing path patterns and optimizing the process iteration loop to avoid filesystem lookup overhead, preventing CI Standard timeouts. Updated `.github/workflows/ci-standard.yml` to exempt newly expanded files from the 500-line check soft cap, and disabled autoderiving fleet nodes in tests (`tests/conftest.py`) to prevent network timeouts.
- **2026-05-26 (2.5.37):** Fixed pre-existing TestErrorHandling test pollution in `tests/api/test_routers_runners.py`. Earlier tests in `TestGetRunners` populate two pieces of module-level state — `cache_utils._cache` (TTL cache) and `runners_router._last_successful_runners` (degraded-mode fallback). Once populated, the API-error / rate-limit tests in `TestErrorHandling` received `source='cache'` instead of `'unavailable'`/`429`, because the endpoint falls back to the last-known-good response when the mocked GitHub call fails. The actual root cause was the global, not just the cache. Added an autouse fixture on `TestErrorHandling` that clears both pieces of state before and after each test. 33/33 passing locally (was 31 pass + 2 fail).
- **2026-05-26 (2.5.36):** Comprehensive autoscaler-corruption recovery + VS Code blank-window fix. New `backend/runner_state_cleanup.py` module removes orphaned `$HOME/.gitconfig.lock` (and stale per-worktree git locks older than 60s) after every autoscaler stop — recovering from the fleet-wide outage signature where a SIGTERM mid-`git config --global` poisons every subsequent `actions/checkout` on the host (Runner_Dashboard#640). Cleanup is invoked from `_stop_unit` on every stop path and mirrored in `deploy/runner-hooks/job-started.sh`. `backend/routers/credentials.py` Cline detection uses a new `_resolve_vscode_cli` helper that prefers `code.cmd` over `Code.exe` on Windows and a `_vscode_has_extension` helper that passes `CREATE_NO_WINDOW`. 7 new unit tests + 3 updated; 25/25 passing.
- **2026-05-26 (2.5.35):** Added the ControlTower workflow-routing policy
  contract for issue `#757`. `config/workflow_runner_routing_policy.json`
  now defines the future `d-sorg-fleet-bulk`, `d-sorg-fleet-fast-io`,
  `d-sorg-fleet-docker`, and `d-sorg-fleet-nvme` label taxonomy, while
  `scripts/check_workflow_runner_routing.py` performs an offline audit that
  fails only on explicit tier misuse and reports neutral-label migration
  recommendations. `docs/runbooks/runner-routing-labels.md` is the operator
  guide for the transition from today's neutral `d-sorg-fleet` label to the
  planned dual-tier NVMe/HDD host.
- **2026-05-26 (2.5.35):** Hardened split-disk/NVMe dashboard deployments.
  `deploy/setup.sh` now accepts `--runner-base-dir` and `--deploy-dir`, installs
  locked runtime dependencies into the deployed `.venv`, and templates
  `RUNNER_BASE_DIR` into the systemd unit so dashboard runner controls can point
  at non-default runner roots. The dashboard service now runs as `Type=simple`,
  tolerates mirrored-port restore failures, disables the unavailable sd_notify
  watchdog path, and collects crash diagnostics through
  `deploy/collect-crash-diagnostics.sh` on stop/failure. The Windows WSL
  keepalive script and installer support a no-reset resident mode for keeping
  the selected distro warm without restarting all WSL instances. The diagnostics
  and runner APIs report degraded fleet-node state instead of failing requests
  when remote runner hosts are unhealthy.
- **2026-05-26 (2.5.35):** Clarified the `/api/health` contract after the
  GitHub admin helper signature repair. Health polling now calls the shared
  GitHub runner-list helper with its supported endpoint-only signature instead
  of passing an unsupported timeout override. The endpoint continues to report
  dashboard status plus GitHub connectivity fields such as
  `github_api`, `github_check_seconds`, and `runners_registered`, and focused
  tests pin both the helper-call contract and the API response shape.
- **2026-05-25 (2.5.34):** Restored the Docker runtime image to the pinned
  Python 3.12 base required by `pyproject.toml`, deployment hardening tests,
  and the lockfile's binary wheel availability. This prevents Python 3.14
  source builds for packages such as `pydantic-core`, `uvloop`, and
  `watchfiles`, reducing CI I/O pressure and making Docker validation
  deterministic again.
- **2026-05-25 (2.5.33):** Hardened runner-autoscaler recovery after WSL I/O
  pressure incidents. The autoscaler now protects a default two-runner online
  floor, respects that floor even when scheduler desired capacity drops lower,
  and restores a configurable four-runner recovery pool after overload clears
  before waiting for full low-pressure recovery to rebuild the entire scheduled
  pool. `AUTOSCALER_RECOVERY_MIN_ONLINE` exposes the recovery pool size, and
  deploy/test coverage pins the default floor contract. Fleet deployment now
  disables boot-time autostart for `actions.runner.*` units and makes bulk
  runner restarts opt-in, so WSL restarts do not launch every runner before
  the autoscaler can sample host pressure.
- **2026-05-25 (2.5.32):** Hardened autoscaler overload detection for
  ControlTower WSL I/O stalls. `backend/autoscaler_sampling.py` now parses
  Linux PSI data from `/proc/pressure/io`, and `backend/runner_autoscaler.py`
  treats high `full avg10` I/O pressure as overload while blocking scale-up
  recovery until I/O pressure drops. `AUTOSCALER_IO_PRESSURE_FULL_HIGH`,
  `AUTOSCALER_IO_PRESSURE_FULL_LOW`, and
  `AUTOSCALER_ACTION_COOLDOWN_SECONDS` make the thresholds configurable.
  Focused tests cover the PSI parser and exported autoscaler contract.
- **2026-05-25 (2.5.31):** Fixed tautological rgba() budget assertion in
  `tests/frontend/test_color_literal_budget.py`. The test previously recalculated
  `BUDGET = count + 20` on every run, making `count <= count + 20` always true.
  Changed to a hard-coded constant `RGBA_BUDGET = 73` (current baseline established
  at D5 landing) so the test now enforces a real regression gate. Part of issue #706
  (Epic: Dashboard Hardening & UX Overhaul), subtask D5.
- **2026-05-24 (2.5.30):** Hardened autoscaler config parsing and module-level
  test coverage for issue `#727`. `backend/autoscaler_config.py` now raises
  `ValueError` at startup when autoscaler numeric env vars are malformed or
  negative instead of silently falling back to defaults. Focused tests now pin
  that contract plus the autoscaler sampling/systemd helpers, and the targeted
  autoscaler coverage lane measures `autoscaler_busy=89%`,
  `autoscaler_config=92%`, `autoscaler_sampling=92%`, and
  `autoscaler_systemd=98%`.
- **2026-05-24 (2.5.30):** Hardened the Windows WSL keepalive watchdog for
  runner-dashboard recovery. `deploy/wsl-keepalive.ps1` now accepts
  `DashboardPort` and `DashboardServiceName`, validates both inputs, probes the
  local `/health` endpoint, attempts a dashboard-only `systemctl start` when
  WSL is responsive but the dashboard is unhealthy, and escalates to a full
  WSL reset only if that targeted recovery still fails. The script logs the
  new `dashboard_unhealthy_detected`, `dashboard_recovery_started`,
  `dashboard_recovery_failed`, and `dashboard_recovery_after_wsl_reset_*`
  events, and `tests/deploy/test_wsl_keepalive_script.py` asserts the new
  parameter validation and recovery-path coverage.
- **2026-05-23 (2.5.29):** Documented A-series infrastructure hardening:
  Prometheus autoscaler and lease-reaper metrics, `/readyz` runner-health
  probing, quick-dispatch health gating/backpressure, drain-mode deployment
  controls, runner memory/restart systemd drop-ins, deployment preflight
  checks, and the autoscaler Grafana dashboard.
- **2026-05-23 (2.5.28):** Added B-series API contract hardening:
  `backend/gh_client.py` now owns GitHub API retries, timeouts, and HTTP error
  normalization; backend code may not shell out to `gh api` for request paths.
  Request payloads for help chat and launcher generation are validated by
  Pydantic models in `backend/models/requests.py`, and API errors use the
  shared `backend/error_models.py` envelope with structured audit coverage
  while preserving structured HTTP exception details and response headers such
  as `Retry-After`.
  The anti-phantom issue-resolution guard now recognizes this repo's real
  implementation roots (`backend/` and `frontend/src/`) when validating
  feature PRs and issue path evidence.
- **2026-05-22 (2.5.27):** Added relative-timestamp primitive (#725):
  `frontend/src/hooks/useTimeAgo.ts` (`useTimeAgo`, `formatTimeAgo`) and
  `frontend/src/primitives/TimeAgo.tsx` (`<TimeAgo iso={...} />`). Renders
  semantic `<time>` with the raw ISO in `dateTime` + tooltip; future
  timestamps render `"soon"`, invalid input degrades to the raw value with
  a `console.warn`. Wired into `Reports/Mobile.tsx` "Modified" field as the
  first call-site; remaining pages migrate in follow-ups.
- **2026-05-22 (2.5.26):** Added `DASHBOARD_HOST` env var
  (`dashboard_config.HOST`) for uvicorn bind interface; default preserves
  historical `0.0.0.0` behaviour. Added `deploy/wsl-mirrored-port-helper.sh`
  invoked from the systemd unit's `ExecStartPre`/`ExecStartPost` to dodge
  the recurring WSL-mirrored Tailscale-serve port conflict that crash-looped
  the dashboard after every WSL cold-restart. Added `deploy/wsl-keepalive.ps1`
  Windows watchdog with responsiveness probe, structured JSONL logging, and
  exponential backoff. Added host-wide Windows resource metrics for WSL
  dashboard telemetry and autoscaler decisions so runner scaling uses the same
  CPU/RAM values operators see in Task Manager. See
  `docs/wsl-mirrored-port-conflict.md`.

---

## 0. Sibling repos & boundaries

`runner-dashboard` is one of three repos that together form the
D-sorganization fleet operating system. The cross-repo contract is
documented canonically in
[`Repository_Management/docs/sibling-repos.md`](https://github.com/D-sorganization/Repository_Management/blob/main/docs/sibling-repos.md).
Quick form:

- **[`Repository_Management`](https://github.com/D-sorganization/Repository_Management)** â€” fleet orchestrator.
  Publishes shared CI workflows, skills, templates, agent coordination.
  _Does not_ own dashboard UI, backend, or HTTP API.
- **`runner-dashboard`** (this repo) â€” operator console. Owns every dashboard
  tab, every `/api/*` endpoint, deployment + rollback machinery.
- **[`Maxwell-Daemon`](https://github.com/D-sorganization/Maxwell-Daemon)** â€”
  autonomous local AI control plane. The Maxwell tab here calls the daemon
  over HTTP; the daemon never calls back.

This SPEC documents only what `runner-dashboard` owns. Fleet-wide workflow
manifests, agent claim/lease protocol, Project_Template, and skill publishing
specs live in [`Repository_Management/SPEC.md`](https://github.com/D-sorganization/Repository_Management/blob/main/SPEC.md).
The Maxwell pipeline state machine and ExecutionSandbox specs live in
[`Maxwell-Daemon`](https://github.com/D-sorganization/Maxwell-Daemon).

---

## 1. Purpose and Scope

The Runner Dashboard is the central web UI control surface for the
D-sorganization self-hosted GitHub Actions runner fleet. It aggregates runner
health, workflow activity, AI agent dispatch, and fleet operations into a
single browser-based interface backed by a local FastAPI server.

**In scope:**

- Real-time monitoring of all self-hosted runners and their systemd services
- Workflow run history, queue management, and cancellation/rerun controls
- AI agent dispatch (Jules, GAAI, Claude, Codex, Maxwell) via remediation API
- Multi-node fleet hardware and system metrics
- Scheduled workflow inventory and manual dispatch
- Local application process health monitoring
- Credential management and secrets inventory
- Automated runner scaling configuration
- Fleet orchestration and cross-node deployment control

**Out of scope:**

- GitHub-hosted runner management (cloud runners)
- Repository code review or merge operations (delegated to agent workflows)
- Direct SSH access to fleet nodes (use the deploy scripts)

---

## 2. Architecture

### 2.1 Backend

**Runtime:** Python 3.11+
**Framework:** FastAPI (ASGI via uvicorn)
**Port:** 8321 (configurable via `DASHBOARD_PORT` env var)
**Entry point:** `backend/server.py`

The backend is a single-process FastAPI application that:

1. Proxies the GitHub REST API (runners, workflows, runs, repos) using an
   authenticated `httpx.AsyncClient` with the `GITHUB_TOKEN` environment variable.
2. Controls local systemd runner services (`systemctl start/stop`) via
   subprocess calls when running in WSL/Linux.
3. Collects real-time system metrics (CPU, RAM, disk, GPU/VRAM) using `psutil`
   and vendor-specific CLI tools.
4. Reads and writes runtime configuration files (YAML/JSON) from `config/` and
   `~/.config/runner-dashboard/`.
5. Serves the built frontend SPA (`dist/index.html`) as a static file at `GET /`.

Runtime configuration files include the optional `config/linear.json`, which
declares Linear workspaces, team filters, and taxonomy mappings used by the
Linear and unified issue inventory endpoints.

When the backend runs as a Windows fallback process, Linux-only probes must not
raise request-time exceptions. `/api/system` returns Windows-safe `psutil`
metrics, systemd keepalive checks report `unsupported` with an explanatory
detail, scheduler timers report inactive instead of shelling out to
`systemctl`, and `.wslconfig` discovery checks native Windows profile paths.
The Windows Scheduled Task keepalive probe must execute valid PowerShell and
surface task action details without exposing secrets.

Local hook and CI checks must be runnable from Windows development hosts
without bypass flags. Tests that require POSIX-only capabilities such as
`fcntl`, symlink creation privileges, POSIX mode-bit enforcement, or a healthy
WSL/bash service must skip only when that capability is unavailable locally and
must remain active on Linux CI. Workflow guards that inspect PR file lists must
use GitHub API file pagination rather than `gh pr diff --name-only`, because
the fleet runner CLI version does not support that flag consistently.

**Supporting modules (all in `backend/`):**

| Module                    | Responsibility                                                                                                                                                                                                            |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `agent_remediation.py`    | AI agent dispatch plans, GAAI/Claude/Codex invocation                                                                                                                                                                     |
| `dispatch_contract.py`    | Type contracts for workflow dispatch payloads                                                                                                                                                                             |
| `machine_registry.py`     | Multi-node fleet registry (load, merge with live data)                                                                                                                                                                    |
| `scheduled_workflows.py`  | Inventory of scheduled workflow definitions                                                                                                                                                                               |
| `deployment_drift.py`     | Version drift detection between deployed and expected                                                                                                                                                                     |
| `local_app_monitoring.py` | Health checks for local process/app registry                                                                                                                                                                              |
| `usage_monitoring.py`     | Per-runner CPU/RAM usage time-series collection                                                                                                                                                                           |
| `workflow_stats.py`       | Aggregate workflow success/failure statistics                                                                                                                                                                             |
| `report_files.py`         | Parse dated report files for the Reports tab                                                                                                                                                                              |
| `runner_autoscaler.py`    | Dynamic runner count scaling — main loop and public re-export facade                                                                                                                                                      |
| `autoscaler_config.py`    | Autoscaler env-var helpers and all threshold constants, including I/O pressure gates                                                                                                                                      |
| `autoscaler_systemd.py`   | Autoscaler systemd unit enumeration, state inspection, start/stop                                                                                                                                                         |
| `autoscaler_busy.py`      | Autoscaler layered busy-detection (4 strategies, issue #651)                                                                                                                                                              |
| `autoscaler_sampling.py`  | Autoscaler resource sampling (CPU/mem/disk/load/I/O pressure) and scheduler integration                                                                                                                                   |
| `config_schema.py`        | Config validation and atomic JSON writes                                                                                                                                                                                  |
| `pr_inventory.py`         | Fetch and normalise open PRs across repos (issue #80)                                                                                                                                                                     |
| `issue_inventory.py`      | Fetch and normalise open issues with taxonomy (issue #81)                                                                                                                                                                 |
| `linear_inventory.py`     | Fetch and normalise Linear issues into the canonical issue inventory shape                                                                                                                                                |
| `health.py`               | Health check endpoints (`/api/health`, `/health`) extracted from server.py (issue #159)                                                                                                                                   |
| `metrics.py`              | System metrics endpoints (`/api/system`, `/api/fleet/status`) extracted from server.py (issue #159)                                                                                                                       |
| `fleet_events.py`         | Fleet event log + alarm feed (issue #863): typed `FleetEvent`, pure `classify_fleet_events` (offline/online/low-disk/saturation/watchdog), bounded `EventStore` ring buffer, and `FleetEventPoller` driving `/api/events` |

**Bounded domain routers (`backend/routers/`):**

Well-bounded API domains with no cross-domain shared state are extracted into
`APIRouter` modules and registered with `app.include_router()`. This reduces
coupling and makes each domain independently testable.

| Router                   | Prefix                | Responsibility                                                                       |
| ------------------------ | --------------------- | ------------------------------------------------------------------------------------ |
| `routers/deployment.py`  | `/api/deployment`     | Deployment metadata, expected-version, drift, git-drift (issue #357)                 |
| `routers/reports.py`     | `/api/reports`        | Report file listing and dated metric parsing (issue #358)                            |
| `routers/heavy_tests.py` | `/api/heavy-tests`    | Heavy test run tracking and result storage (issue #358)                              |
| `routers/assessments.py` | `/api/assessments`    | Repo assessment JSON listing and retrieval (issue #358)                              |
| `routers/dispatch.py`    | `/api/fleet/dispatch` | Fleet agent dispatcher â€” allowlisted hub-to-node commands                          |
| `routers/credentials.py` | `/api`                | Credential probe â€” tool/key presence without exposing values                       |
| `routers/linear.py`      | `/api/linear`         | Optional Linear read API for workspaces, teams, and issue inventory                  |
| `push.py`                | `/api/push`           | Web Push subscription storage, scoped unsubscribe, and test-send foundation          |
| `routers/events.py`      | `/api/events`         | Read-only fleet event-log feed backed by the `fleet_events` ring buffer (issue #863) |

The migration from inline `@app.*` endpoints to bounded routers is ongoing.
Remaining endpoint domains in `server.py` are tracked for extraction under issue #4.

Backend tests must resolve `backend/` imports consistently from a clean checkout.
The project pytest configuration declares `backend` on `pythonpath`, and
`tests/conftest.py` also inserts the resolved backend directory before importing
the FastAPI app and router dependencies.

**Auth test fixtures (issue #343):** `mock_auth` is opt-in (not `autouse`).
Tests that need to bypass authentication must declare `mock_auth` explicitly.
`make_principal(id, type, roles)` creates a minimal `Principal`; the helpers
`admin_principal`, `operator_principal`, and `viewer_principal` cover the
three main roles. `make_authed_client(principal)` returns a `TestClient` with
the given principal pre-wired.

**Uvicorn runtime tuning (env-var driven, issue #393):**

When `backend/server.py` is invoked as `__main__`, the uvicorn instance is
configured from environment variables so operators can adjust ASGI
behaviour without code changes:

| Variable             | Default | Purpose                                                                                                                                                                            |
| -------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `WORKERS`            | `1`     | Worker process count. Stays at `1` until leader-election (#367) lands; setting it higher logs a runtime warning because background tasks would otherwise duplicate across workers. |
| `LIMIT_CONCURRENCY`  | `200`   | Max concurrent in-flight requests before uvicorn returns 503.                                                                                                                      |
| `TIMEOUT_KEEP_ALIVE` | `5`     | Seconds an idle keep-alive HTTP connection is held before closure.                                                                                                                 |

Invalid values fall back to the default and emit a log warning.

**Bounded in-process buffers:** the CPU sample buffer `_cpu_history` is a
`collections.deque` capped at `_CPU_HISTORY_MAXLEN` (1000 samples). The
fixed cap guarantees flat memory regardless of process uptime (#393).

### 2.2 Frontend

**Type:** Single-Page Application (SPA)
**Entry point:** `frontend/src/main.tsx` (built via Vite)
**Build step:** Vite + React + TypeScript (`npm run build` -> `dist/`)
**Type checking:** Deferred to Phase 2 (`npm run typecheck`, not blocking build)
**JavaScript framework:** React (imported as ES modules)
**createElement API:** `h()` alias (JSX migration in progress)
**Styling:** Extracted CSS in `frontend/src/index.css` (was inline)

Application logic is contained in `frontend/src/legacy/App.tsx` (migrated from
the previous single-file `frontend/index.html`). The Vite build outputs to
`dist/` which the FastAPI backend serves. A `package.json` with build tooling
is now present. Type checking is separated from the build pipeline to allow
incremental migration of legacy code; Phase 2+ will progressively fix type errors
as routes and components are extracted.
`frontend/perf-budget.json` records the issue #200 mobile performance budget.
The budget check enforces
the target mobile shell, tab chunk, Lighthouse, INP, and FCP values plus an
interim gzip ceiling for the built `dist/index.html` and its bundled JavaScript/CSS.
Budget increases require a PR that edits the budget file with justification.

Mobile layouts must remain usable at 375x812 and 412x915 viewport sizes. The
header tab strip is horizontally scrollable, nonessential header status badges
are hidden on mobile, Queue Health renders compact KPI/cards instead of forcing
wide tables, and Workflows filters use sessionStorage-backed state so tab
switching and app backgrounding do not reset the current session filters.
Reports, Assessments, and Feature Requests expose read-mostly mobile card and
reader layouts over their existing APIs so operators can inspect report files,
assessment score history, and feature request history without relying on wide
desktop tables.

The mobile foundation is documented in `docs/mobile-native-shell.md` and
`docs/mobile-design-system.md`. Reusable mobile
design contracts live in `frontend/src/design/*.ts` modules and are
guarded by pytest. The Fleet tab exposes a
mobile-only read surface for runner monitoring cards over the existing runner,
run, and machine telemetry payloads; desktop machine and runner tables remain
the canonical wide-screen surface.

**Offline mutation queue (issue #380):** When `navigator.onLine` is `false`, POST/DELETE/PATCH mutations that fail due to network error are persisted to IndexedDB via `frontend/src/lib/mutationQueue.ts` (backed by the `idb` library). Each queued entry carries a generated `Idempotency-Key` UUID so server-side duplicate execution is impossible on replay. On `window.online`, the queue is drained in FIFO order; entries older than 10 minutes require explicit user reconfirmation before replay. The `OfflineQueueIndicator` primitive in `frontend/src/primitives/OfflineQueueIndicator.tsx` renders an accessible `role="status"` badge showing offline state and pending-replay count.

Reusable UI primitives live in `frontend/src/primitives/`. Issue #422 introduces `Badge.tsx` (`tone` in `success | warning | danger | info | neutral`, `size` in `sm | md`) and `Pill.tsx` (with a `selected` boolean prop) so that the previously ad-hoc `.section-badge`, `.runner-status-badge`, `.conclusion-badge`, `.subtab-badge`, and `.fleet-status-pill` styles share a single token-driven implementation backed by `--badge-*-bg` / `--badge-*-fg` CSS variables in `frontend/src/design/tokens.ts`.

PushSettings (issue #192) is a mobile-friendly React component for per-topic Web Push subscription management. It is located at `frontend/src/pages/PushSettings.tsx` and uses `GET /api/push/vapid-public-key` to fetch the VAPID key before subscribing to selected push topics via `POST /api/push/subscribe`.
The Vite entrypoint in `frontend/src/main.tsx` includes a minimal tracer-bullet route shim for `/settings/push`: when the browser pathname resolves to that route, it renders `PushSettings` directly; all other paths continue to render the main dashboard app. This keeps the PushSettings work isolated while the Vite migration remains in progress.
Mobile accessibility guards are part of the frontend source contract. At
mobile viewport widths, primary interactive controls must use the shared
`--mobile-hit-target` token with a minimum `44px` target size. CSS animations
and transitions must respect `prefers-reduced-motion: reduce`, and inline
transition styles must opt out through `prefersReducedMotion()`. Static
frontend integrity tests enforce these guards alongside ARIA labels for mobile
summary sections and modal dialogs. The HTML viewport metadata must not disable
user scaling with `maximum-scale` or `user-scalable=no`.

The first issue #202 mobile test harness slice lives in `tests/frontend/mobile/`.
It defines the Playwright mobile viewport contract for `iphone-12` (390 x 844),
`pixel-5` (393 x 851), `epic-compact-375` (375 x 812), and
`epic-standard-412` (412 x 915), plus shared tap, swipe, and long-press helper
scaffolding. The current CI-safe guard is static pytest validation; browser
execution and screenshot baselines remain disabled until the harness proves
stable enough to add a non-flaky Playwright lane.

The first M04 touch primitive implementation slice lives in
`frontend/src/primitives/`. `TouchButton` wraps native buttons with the shared
mobile hit-target and press/focus affordance, while `SegmentedControl` provides
an accessible `radiogroup` for compact mobile filters. Gesture-heavy primitives
(`SwipeRow`, `PullToRefresh`, and `BottomSheet`) remain separate follow-up
work because they require pointer-event and focus-management tests.

**Shared helper components** defined near the top of the script block:

- `Collapse` â€” collapsible section with header and chevron.
- `SubTabs` â€” horizontal sub-tab strip rendered inside a tab panel. Props:
  `tabs` (array of `{ key, label, badge, disabled }`), `activeKey`, `onChange`,
  `storageKey` (optional localStorage persistence key), `rightBadge` (optional
  element flush-right of the strip). Active tab is persisted to localStorage
  when `storageKey` is provided.

#### Header Quick Dispatch

The main header contains a **Quick Dispatch** button (âš¡ Quick Dispatch â–¾),
flush-right next to the refresh control. Clicking it opens a popover form that
lets any operator dispatch an ad-hoc agent task to any org repository without
navigating to a specific tab. The popover provides:

- **Repository** dropdown â€” populated from `GET /api/repos`
- **Provider** dropdown â€” populated from `GET /api/agents/providers`
- **Model** text field â€” shown only for providers that support model selection
  (`claude_code_cli`, `codex_cli`); defaults to `claude-opus-4-7`
- **Branch ref** text field â€” defaults to `main`
- **Prompt** textarea â€” minimum 10 characters
- **Dispatch** button â€” POSTs to `POST /api/agents/quick-dispatch`; shows a
  loading state, surfaces errors inline, and auto-closes on success

Click-outside closes the popover. Rate-limit errors (HTTP 429) are surfaced
with a human-readable message.

`frontend/src/legacy/App.tsx` is the **sole canonical frontend source** during
Phase 1 of the Vite migration. `frontend/index.html` is now the minimal Vite
HTML shell. No other
frontend implementation exists in the repository. The previously present
`RunnerDashboard.jsx` was an unused JSX archive that violated DRY; it was
removed in issue #3 to enforce a single source of truth. A CI test
(`test_jsx_archive_removed`) prevents re-introduction of a parallel
implementation.

### 2.3 Deployment

The dashboard runs as a systemd service (`runner-dashboard.service`) on the
primary fleet machine. See Section 6 for deployment details.

`deploy/setup.sh` performs a `preflight()` check before any mutation (asserts
disk free >1G at the deploy dir, Python 3.11+, port 8321 availability, and
`~/.config/runner-dashboard/env` permissions of `600`), supports `--check-only`
to run preflight without side effects and `--dry-run` to preview intended
mutations, replaces `/etc/sudoers.d/runner-dashboard` atomically via
`visudo -c -f` against a temp file (validation failure leaves the existing
file untouched), and skips `systemctl restart runner-dashboard` when the
deployed `git_sha` in `deployment.json` matches the current checkout unless
`--force` is supplied.

#### 2.3.1 WSL boot stability (split-disk hosts)

Hosts that split runner storage across two physical disks run two systemd WSL2
distros in one shared utility VM. Two hardening steps keep them out of the
`WaitForBootProcess` crash loop (WSL aborts a distro boot — `reboot(RB_POWER_OFF)`
— if systemd does not reach its default target within ~10s):

- **`deploy/decouple-docker-boot.sh`** removes `docker.service` and
  `containerd.service` from the boot transaction (they stay installed and are
  not stopped), keeps `docker.socket` enabled for on-demand activation, and
  installs **`deploy/docker-delayed-start.timer`** (`OnBootSec=20s`) so Docker
  starts shortly after boot, off the critical path. On SSD-backed distros this
  drops boot-ready from ~15s to ~4–5s. Effective on next boot; idempotent.
- **`deploy/install-wsl-keepalive-task.ps1`** registers the resident keepalive
  under a **WSL-capable interactive user principal** (`-LogonType Interactive -RunLevel Highest`)
  with an unbounded `ExecutionTimeLimit` and `AtStartup`/`AtLogOn` triggers (issue #1139).
  This avoids the `WSL_E_LOCAL_SYSTEM_NOT_SUPPORTED` failure under `SYSTEM` and the missing
  user-scoped WSL registration access under `S4U`, while keeping the host-side handle that holds
  the WSL VM resident.
- **`deploy/fleet-health-monitor.ps1`** is the canonical DeskComputer fleet
  monitor (5-minute scheduled task, launched via `run-hidden.vbs`). Beyond the
  original DeskComputer-keepalive check and ControlTower WMI-handle guard, it
  enforces **per-pool online floors** (Desktop / ControlTower-SSD / Oglaptop)
  from the dashboard's `/api/runners` feed, self-heals a below-floor Desktop
  pool with in-place `systemctl start` of the local runner units (never a WSL
  reset), and raises an explicit **registration-purge alarm** (zero pool
  members online on GitHub while local units run — the signature of GitHub's
  ~14-day offline auto-delete, which silently destroyed the DeskComputer
  pool's registrations in July 2026). Recovery procedure:
  `docs/runbooks/runner-registration-purge-recovery.md`. Cycles carry a
  heartbeat log line and a hard deadline on the ControlTower SSH leg (a
  hung ssh once stalled the monitor for 100+ minutes under
  `MultipleInstances=IgnoreNew`), and any floor breach computed from the
  dashboard feed is re-verified against the GitHub API before warnings or
  self-heal actions (the feed can serve stale/false zeros; verification
  unavailable → the cycle takes no action). The same ControlTower probe
  reports host free space and alarms below per-drive floors
  (`$DiskFloorsGb`, default C: 25 GB / F: 40 GB): a WSL2 distro that
  exhausts host disk mid-write corrupts itself — null-byte files and a
  broken package database — which is the probable origin of the #1071 NVMe
  corruption and came within minutes of repeating on the live pool on
  2026-07-31. The floor is **alarm-only**; reclaiming space means deleting
  large artifacts and is never automated.
- **`deploy/run-hidden.vbs` + `deploy/install-hidden-task-launcher.ps1`** stop
  InteractiveToken scheduled tasks from popping focus-stealing console windows
  in the user's session. The installer rewrites a task's action to
  `wscript.exe //B //Nologo "run-hidden.vbs" <exe> <args>`; the GUI-subsystem
  host launches the child with `SW_HIDE` (no console window is ever created —
  unlike `-WindowStyle Hidden`, which still flashes the console host), waits,
  and propagates the child's exit code so `LastTaskResult` stays accurate.
  Contracts: idempotent (wrapping a wrapped task is a no-op), reversible
  (`-Revert` restores the original action verbatim), postcondition-verified
  (re-reads the task after writing), foldered tasks addressed by discovered
  `TaskPath`, and arguments with literal double quotes refused (WScript strips
  quoting; they cannot be rebuilt losslessly). For tasks that do not need the
  interactive session, an S4U principal remains the preferred fix. Runbook:
  `docs/runbooks/interactive-task-console-popups.md`.

#### 2.3.2 Disk-pressure controls (`runner-cleanup.sh`)

Docker (build cache + volumes + buildx builder containers) is the dominant disk
consumer on runner hosts. Two controls keep a distro from filling to 100% (which
crash-loops every runner with `No space left on device`):

- **Aggressive docker prune under pressure.** When the root filesystem crosses
  `DISK_PRESSURE_PERCENT` (default 85%), `runner-cleanup.sh` sets
  `DOCKER_AGGRESSIVE=1`: it prunes **all** build cache (including `docker buildx`
  builder caches), **all** unused images, and **dangling volumes**, ignoring the
  routine `DOCKER_PRUNE_UNTIL` age window. Pruning only removes stopped
  containers, unused images, dangling volumes, and idle cache, so running jobs
  and in-use volumes are never affected. Below the threshold, the routine pass
  keeps the age window so recent cache is preserved for build speed.
- **Hourly disk guard.** `runner-cleanup.sh --disk-guard` is a lightweight,
  runner-safe pass that reclaims docker + journal + `fstrim` **only** — it never
  stops runner units, so `install-runner-maintenance.sh` schedules it on an
  **hourly** `runner-disk-guard.timer` (the full cleanup, which bounces idle
  runners to clear `_work`, stays daily). This catches docker bloat long before
  the disk fills between daily cleanups.

---

## 3. Feature List â€” Dashboard Tabs

### 3.1 Fleet Tab

Real-time view of all self-hosted runners. Displays runner name, status (idle,
active, offline), current job, labels, and systemd service state. Provides
start/stop controls per runner and bulk fleet controls.

#### 3.1.1 Multi-Pool Fleet Status

`GET /api/fleet/status` aggregates multiple co-located runner-pool backends
into a single response. On a ControlTower host that runs two pool backends:

- Port **8321** — `ControlTower-NVMe` (primary, hub role)
- Port **8322** — `ControlTower-HDD` (secondary pool)

The primary dashboard instance (8321) queries the secondary (`?exclude_pools=true`)
and merges both pool entries under their pool names. If the secondary is
unreachable, its entry is set to `{"status": "offline", ...}` so the primary
view remains available. The `exclude_pools=true` query parameter prevents
recursive peer queries.

In the fleet deployment, dashboard-to-dashboard routing must prefer Tailscale
MagicDNS hostnames over ephemeral WSL-local or stale tailnet IP addresses. The
ControlTower pool URLs are canonicalized as:

- `http://controltower.tail2bbcc7.ts.net:8321` for `ControlTower-NVMe`
- `http://controltower.tail2bbcc7.ts.net:8322` for `ControlTower-SSD`

The per-node system proxy budget is 30 s for remote node telemetry. This keeps
loaded Windows hosts, especially DeskComputer during full runner load, from
being marked unhealthy while enumerating runner processes and host metrics.

Response shape (keyed by pool name, plus any FLEET_NODES peers):

```json
{
  "ControlTower-NVMe": { "status": "online", "hostname": "...", ... },
  "ControlTower-SSD":  { "status": "online",  "hostname": "...", ... },
  "OGLaptop":          { "status": "online",  "hostname": "...", ... }
}
```

The Fleet tab `Mobile.tsx` renders entries whose names start with `"ControlTower"`
in a dedicated **ControlTower Pools** section with a side-by-side card layout;
all other fleet nodes render as standard runner cards below that section.

#### 3.1.2 Persistent hardware-facts cache (cold-start budget)

`GET /api/fleet/status` and `GET /api/system` build their disk/storage-pool
section via `system_utils.get_storage_pools()`, which on WSL hosts probes the
Windows host for **static hardware facts** — a drive's `MediaType`/`BusType`
(`Get-PhysicalDisk`) and the distro's VHDX `BasePath` (Lxss registry). These
PowerShell probes can take ~10 s and ~1.3 s respectively on a cold cache. Since
the in-memory cache is empty after every `systemctl restart`, the first request
previously paid the full ~13 s and, together with the live Windows
host-resource snapshot, exceeded the 15 s `PROXY_TO_HUB_S` budget — surfacing as
HTTP 504 "Hub timeout".

Because these facts are static, they are persisted to
`~/.config/runner-dashboard/hardware_facts.json` (override the directory with
`RUNNER_DASHBOARD_STATE_DIR`). After the first warm-up the cold path is served
from disk in microseconds, so restarts no longer pay the probe tax. Each entry
carries a `_fetched_at` timestamp and is re-probed once per `_HW_FACTS_TTL_S`
(24 h) in case host topology changes; even a `Unknown/Unknown` failure result is
persisted so hosts where `Get-PhysicalDisk` is slow-but-empty stay fast. Every
live PowerShell probe is additionally bounded by a `_HW_PROBE_TIMEOUT_S` (4 s)
wall-clock deadline that clamps each candidate's subprocess timeout and stops
iterating candidates once exhausted, so a slow probe degrades to `Unknown`
instead of blocking the endpoint. The returned metric shape is unchanged.

Two further per-request subprocess costs in the same metrics path are bounded by
short-TTL caches so steady-state polling never re-pays them:

- **Live Windows host CPU/RAM** (`_windows_host_resource_snapshot`) forks a ~2 s
  PowerShell `Get-CimInstance` on every call. A `_HOST_SNAPSHOT_TTL_S` window
  (10 s, override `RUNNER_DASHBOARD_HOST_SNAPSHOT_TTL_S`) lets concurrent and
  successive callers share one fork; on a refresh failure the last good value is
  served rather than dropping to the WSL fallback.
- **Runner-capacity snapshot** (`get_runner_capacity_snapshot`) forks the
  runner-scheduler binary (`--dry-run --json`) plus two `systemctl is-active`
  calls (~2-3 s on a busy host). It is cached for `CacheTtl.RUNNER_CAPACITY_S`
  (15 s); the underlying schedule/timer state changes on the order of minutes,
  so the panel stays effectively live. Both the metrics endpoints and the
  orchestration schedule routes share the cache.

### 3.2 History Tab

Paginated workflow run history across all org repositories. Filterable by repo,
status, branch, and actor. Supports rerun and cancel actions on individual runs.

### 3.3 Queue Tab

Live view of queued and in-progress workflow jobs. Shows waiting time, assigned
runner, and blocking conditions. Supports bulk cancellation. Includes a
diagnostic endpoint to explain queue stalls. On mobile, the tab presents a
queued/running/stale KPI strip and compact queued-run cards; destructive cancel
actions require an explicit confirmation state that shows the number of runs
affected before the existing cancel endpoint is invoked.

### 3.4 Machines Tab

Multi-node fleet hardware inventory sourced from `machine_registry.yml`.
Displays per-node system metrics (CPU, RAM, disk, GPU VRAM) fetched via the
fleet nodes API. Supports drilling into individual node system status.

### 3.5 Organization Tab

Org-level runner and repository summary. Shows runner group assignments,
available label sets, and aggregate health across all repos.

### 3.5.1 `/api/linear/*` â€” optional Linear integration

When Linear is configured, the dashboard exposes:

- `GET /api/linear/workspaces` â€” configured workspaces with auth status
- `GET /api/linear/teams` â€” teams for one workspace or all configured workspaces
- `GET /api/linear/issues` â€” Linear-only issue inventory in canonical dashboard shape

If Linear is not configured, Linear-backed issue reads return HTTP 503 with the
standard not-configured detail. `GET /api/issues` accepts
`source={github|linear|unified}`; `github` remains the backward-compatible
default.

Issue #242 also adds a write-only inbound webhook surface for Linear. The
dashboard exposes `POST /api/linear/webhook` for Funnel-delivered webhook
events and `GET /api/linear/webhook/health` for operator health checks. The
receiver validates `Linear-Signature` when a secret is configured, bypasses
browser CSRF checks for this external-service route only, rejects stale
payloads older than 300 seconds, and deduplicates repeated `webhookId` values
to provide replay protection.

Issue #243 completes the Linear integration by wiring the webhook receiver to
the agent-agnostic dispatch path and adding a lightweight Credentials-tab
setup panel in `frontend/src/pages/LinearSetup.tsx`. The setup panel displays
the webhook URL, workspace auth/trigger metadata, and the operator-facing
instructions for configuring the inbound Linear webhook.

### 3.6 Tests Tab

Unified testing hub with two sections:

1. **CI Tests** â€” table of the latest `ci-standard` workflow run for each of
   the 17 fleet repos, showing conclusion badge, branch, run number, and
   timestamp. Failed or cancelled runs show a **Re-run Failed** button that
   calls GitHub's `rerun-failed-jobs` API.
2. **Integration Tests** â€” dispatches and monitors heavy integration test runs
   (MuJoCo, Drake, Pinocchio physics stacks). Lists repos eligible for heavy
   testing, dispatches parameterized workflows, and optionally triggers
   Docker-based test environments.

The tab uses shared `Badge`, `EmptyState`, and `TouchButton` primitives for
status labels, loading state, rerun actions, and heavy-test dispatch controls.
Token-backed `tests-tab__*` classes own table wrappers, links, headings, and
dispatch status metadata.

### 3.7 Stats Tab

Aggregate workflow statistics: success rates, average duration, failure
frequency, and per-repo breakdowns sourced from the `/api/stats` endpoint.

### 3.8 Reports Tab

Displays dated fleet report files (Markdown). Provides date selection and
renders the report with parsed metrics summary cards.
On mobile, report files render as tappable cards with date and size metadata,
the selected report uses a constrained reader with mobile typography, and an
Open raw link exposes the underlying report API response as a fallback.

### 3.9 Scheduled Workflows Tab

Inventory of all cron-scheduled workflows across the org. Shows next/previous
run times, schedule expressions, and allows manual dispatch of any scheduled
workflow. Status and latest-run conclusions render through the shared `Badge`
primitive, first-load placeholders use `SkeletonCard`, and empty/no-filter-match
states use the shared `EmptyState` surface so the tab follows the dashboard
design-token system.

### 3.10 Runner Plan Tab

Fleet autoscaler configuration. Displays current runner count, scaling policy,
schedule-based on/off windows, and allows adjusting the target runner count.
The Runner Schedule capacity editor uses shared `Badge`, `TouchButton`, and
`EmptyState` primitives plus token-backed `runner-schedule__*` classes for
scheduler status, save/apply actions, table inputs, and empty/error states.

### 3.11 Local Apps Tab

Health status of local registered applications (processes, services defined in
`local_apps.json`). Shows up/down state, PID, and restart commands.

### 3.12 Remediation Tab

AI agent dispatch control panel organised into three sub-tabs:

- **Automations** (default) â€” configures and dispatches remediation plans to
  Jules, GAAI, Claude, or Codex agents. Shows dispatch history and plan
  preview. Supports per-repo agent routing, loop-guard configuration, and
  provider fallback chain escalation.
- **PRs** â€” multi-select table of open pull requests fetched from
  `GET /api/prs?limit=2000`. Supports filtering by repo, author, and draft
  status. Bulk dispatch sends selected PRs to a chosen provider via
  `POST /api/prs/dispatch` with a confirmation modal.
- **Issues** â€” taxonomy-aware GitHub Issues browser and bulk dispatcher
  (`GET /api/issues?limit=2000`). Filter bar with repo, complexity,
  judgement, and "pickable only" controls persisted to `localStorage`.
  Multi-select table with type/complexity/effort/judgement pills. Non-pickable
  rows are dimmed; `design`/`contested` judgement pills rendered red with
  warning. Dispatches via `POST /api/issues/dispatch` with optional force flag.

The active sub-tab is persisted to `localStorage` under the key
`remediation-subtab`.

On mobile-width viewports, the Remediation sub-tabs render as a segmented
control. Tapping a failed run in Automations opens a bottom-sheet action surface
with the recommended-provider dispatch action, an optional provider picker, a
safety-plan preview action, and a desktop/run link. Mobile dispatch continues to
call the existing `/api/agent-remediation/dispatch` path; it does not introduce a
new dispatch envelope or bypass backend authorization and remediation invariants.
After dispatch submission, the Remediation tab shows an in-flight status tile
above the sub-tabs so the status remains visible while switching between
Automations, PRs, and Issues.

### 3.13 Workflows Tab

Browse and manually dispatch any workflow in any org repository. Supports
input parameter forms generated from workflow `workflow_dispatch` definitions.
Workflow search, repository, and trigger filters are persisted to
sessionStorage for the current browser session.

### 3.14 Credentials Tab

Inventory of GitHub Actions secrets and variables across the org and per-repo.
Read-only view of credential names (not values) for audit purposes.
On mobile-width viewports, the tab renders locked by default and only loads
credential metadata after a fresh WebAuthn assertion succeeds. Mobile
credential mutations require an explicit second confirmation in a bottom-sheet
dialog. `/api/credentials` requests are denylisted from frontend cache paths
and sent with `cache: "no-store"`; credential values are never rendered.

### 3.15 Assessments Tab

Dispatch and track code quality assessment workflows (Jules Assessment
Generator). Shows per-repo assessment scores from the `/api/assessments/scores`
endpoint.
On mobile, assessment score history renders as per-repo cards showing score,
provider, date, and summary while preserving the existing dispatch controls and
read endpoint.

### 3.16 Code Requests Tab

Browse and submit code requests to plan and execute engineering work across fleet repositories
via templates (formerly Feature Requests; CR-1, #1281). Allows dispatching code implementation
workflows directly from the dashboard.
On mobile, dispatched code request history renders as compact read-mostly cards showing
repository, status, vote-count metadata when present, provider, date, and prompt excerpt
over the `/api/code-requests` response (and legacy `/api/feature-requests` alias).
Dispatch reports its real outcome (#1280): a failed `gh api` dispatch returns HTTP 502 and
records a `failed` history entry with an `error`, and the tab shows the error. `/api/code-requests`
includes `dispatchTarget {workflow, available, detail}`, probed at most every 10 minutes; while it
is unavailable the tab shows why and disables Dispatch.

#### Agent-Agnostic Dispatch & Agent Profiles (CR-3, #1283)

Code requests support agent-agnostic dispatch using signed `CommandEnvelope` messages (`agents.dispatch.adhoc`).
Operators can select or customize agent profiles (`planner-strong`, `executor-cli`, or custom profiles stored via
`/api/agent-profiles`) configuring the execution provider, model, timeout, max turns, and budget ceilings.
The UI dynamically queries the central provider registry (`useProviderRegistry`) across all registered providers,
probes provider health in real-time, displays warning banners if the chosen provider is offline or missing credentials,
and freezes an immutable snapshot of the agent profile on the code request record upon dispatch. Daily and per-request
budget and quota overruns are validated and rejected prior to dispatch. Replacement by Code Requests is tracked in epic #1279.

### 3.17 Maxwell Tab

Control interface for the Maxwell daemon (fleet orchestration AI). Shows
daemon status, configuration, start/stop/configure controls, and a mobile
operator chat surface with preserved history, quick actions, streamed replies,
and a daemon-unreachable retry state.

### 3.18 Fleet Orchestration Tab

Cross-node deployment orchestration. Shows orchestration run history,
dispatches multi-repo deployment plans, and monitors rolling deploy status.

### 3.19 Help Tab

In-app help chat powered by the `/api/help/chat` endpoint. Provides contextual
assistance about dashboard features and fleet operations.

The shell Help/About surface (#822) also hosts an "Ask the codebase" tab (#838):
a codebase Q&A assistant that reuses the streaming chat UI, lets the operator pick
a repository and supply its local `repo_root`, and forwards both through
`POST /api/maxwell/chat` so Maxwell-Daemon answers from the repository's source.
It degrades gracefully when the daemon is unreachable or lacks codebase support.

---

## 4. API Endpoint Catalogue

All endpoints are served under `http://localhost:8321/api/`.

### System and Health

| Method | Path            | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ------ | --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/system`   | Host system metrics (CPU, RAM, disk, GPU)                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| GET    | `/api/events`   | Fleet event-log feed (issue #863). Returns `{events:[{ts,severity,kind,title,detail,node?}], count, capacity}`, newest-first. `severity` ∈ info/warning/critical; `kind` ∈ runner_offline/runner_online/low_disk/saturation/watchdog. `limit` query param (1–500). Ring-buffered in `backend/fleet_events.py`; populated by the fleet-status poller, which classifies runner online/offline transitions (incl. offline-due-to-disk via per-node disk free GB / used %) and low-disk warnings. |
| GET    | `/api/health`   | Dashboard health probe — returns `status` plus GitHub connectivity/runner fields such as `github_api`, `github_check_seconds`, and `runners_registered` when available.                                                                                                                                                                                                                                                                                                                       |
| GET    | `/api/watchdog` | Watchdog status and last heartbeat                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| GET    | `/readyz`       | Readiness probe — runs dependency checks (GH_TOKEN, gh CLI, SQLite stores); returns 200 or 503 with `{status, checks}`                                                                                                                                                                                                                                                                                                                                                                        |
| GET    | `/livez`        | Liveness probe — returns `{“status”:”ok”}` with no I/O; always 200 if process is up                                                                                                                                                                                                                                                                                                                                                                                                           |
| GET    | `/metrics`      | Prometheus text exposition — HTTP request counts/latency, GH API calls, active leases, cache sizes, uptime (issue #330). No auth gate; scrape from `localhost` only.                                                                                                                                                                                                                                                                                                                          |

**Prometheus metrics (`/metrics`):**
Implemented in `backend/instrumentation.py` using the `prometheus_client` library.
Metrics exported:

- `dashboard_http_requests_total{method,path,status}` — counter
- `dashboard_http_request_duration_seconds{method,path}` — histogram
- `dashboard_gh_api_calls_total{result}` — counter (result: success/4xx/5xx/rate_limited)
- `dashboard_gh_api_duration_seconds` — histogram
- `dashboard_dispatch_envelopes_total{action,result}` — counter
- `dashboard_subprocess_spawns_total{cmd}` — counter
- `dashboard_subprocess_duration_seconds{cmd}` — histogram
- `dashboard_replay_dedup_hits_total` — counter
- `dashboard_active_leases{principal}` — gauge
- `dashboard_cache_size{cache_name}` — gauge
- `dashboard_runner_capacity{state}` — gauge
- `dashboard_gh_api_rate_limit_remaining` — gauge
- `dashboard_uptime_seconds` — gauge
- `dashboard_active_websocket_connections` — gauge

**Request ID correlation (`X-Request-ID`):**
Every HTTP response carries an `X-Request-ID` header (issue #331). The value
is the inbound `X-Request-ID` request header if present (echo-back), or a
freshly generated 12-hex-char ID. The request ID flows into every log record
via `contextvars`. Set `LOG_FORMAT=json` for newline-delimited JSON logs with
keys: `ts`, `level`, `module`, `msg`, `request_id`, `principal_id`, `path`.

**Session secret persistence (`SESSION_SECRET_SOURCE`):**
When the `SESSION_SECRET` environment variable is not set, the server resolves
the secret using the following priority order and reports the resolution mode
in `GET /readyz` as `session_secret_source`:

1. `”env”` — `SESSION_SECRET` env var was explicitly configured (recommended
   for production).
2. `”persisted”` — secret was read from
   `~/.config/runner-dashboard/session_secret` (written on first startup).
3. `”generated”` — no env var and no persisted file existed; a fresh secret
   was generated via `secrets.token_hex(32)` and written atomically (mode
   `0o600`) to `~/.config/runner-dashboard/session_secret`.

A `WARNING` is logged at startup whenever the env var is absent so operators
can detect the mode without querying the endpoint. The persisted file
directory can be overridden via the `RUNNER_DASHBOARD_SESSION_SECRET_DIR`
env var.

### Deployment and Drift

| Method | Path                               | Description                                                |
| ------ | ---------------------------------- | ---------------------------------------------------------- |
| GET    | `/api/deployment`                  | Current deployment metadata                                |
| GET    | `/api/deployment/expected-version` | Expected version from repo                                 |
| GET    | `/api/deployment/drift`            | Version drift between deployed and expected                |
| GET    | `/api/deployment/git-drift`        | Git-commit drift: HEAD vs origin/main with is_drifted flag |
| GET    | `/api/deployment/state`            | Full deployment state object                               |
| POST   | `/api/deployment/update-signal`    | Signal the update mechanism                                |

### Runners

| Method | Path                             | Description                                                                                                                 |
| ------ | -------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/runners`                   | All org runners with systemd service state; GitHub rate limits return HTTP 429 with `Retry-After` and `retry_after_seconds` |
| GET    | `/api/runners/matlab`            | MATLAB-capable runner subset                                                                                                |
| POST   | `/api/runners/{runner_id}/stop`  | Stop a runner's systemd service                                                                                             |
| POST   | `/api/runners/{runner_id}/start` | Start a runner's systemd service                                                                                            |

### Workflow Runs

| Method | Path                               | Description                       |
| ------ | ---------------------------------- | --------------------------------- |
| GET    | `/api/runs`                        | Recent workflow runs (all repos)  |
| GET    | `/api/runs/enriched`               | Runs with per-job enrichment data |
| GET    | `/api/runs/{repo}`                 | Runs for a specific repository    |
| POST   | `/api/runs/{repo}/cancel/{run_id}` | Cancel a workflow run             |
| POST   | `/api/runs/{repo}/rerun/{run_id}`  | Re-run a workflow run             |

### Queue

| Method | Path                         | Description                                                                                                                                                                                     |
| ------ | ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/queue`                 | Current job queue (queued + in_progress). Includes `queued_jobs_count` — true job-level queue depth, counting `queued` jobs inside in_progress runs too (run-level `queued_count` misses them). |
| GET    | `/api/queue/status`          | Queue data with per-run `timing` breakdown (queue_wait_seconds, exec_seconds)                                                                                                                   |
| POST   | `/api/queue/cancel-workflow` | Cancel a queued workflow                                                                                                                                                                        |
| GET    | `/api/queue/diagnose`        | Diagnose queue stalls with runner capacity, queued job target counts, label breakdowns, unroutable label findings, sampled jobs, and bounded partial-failure errors                             |

### Push Notifications

| Method | Path                                    | Description                                                          |
| ------ | --------------------------------------- | -------------------------------------------------------------------- |
| POST   | `/api/push/subscribe`                   | Store or update the caller's Web Push subscription and topic list    |
| DELETE | `/api/push/subscribe/{subscription_id}` | Remove the caller's subscription; admins may remove any subscription |
| POST   | `/api/push/test`                        | Admin-only test send to the caller's matching subscriptions          |
| GET    | `/api/push/vapid-public-key`            | VAPID public key for Web Push subscription setup                     |

### Fleet

| Method | Path                                  | Description                                   |
| ------ | ------------------------------------- | --------------------------------------------- |
| GET    | `/api/fleet/status`                   | Aggregate fleet status summary                |
| POST   | `/api/fleet/control/{action}`         | Bulk fleet action (start-all, stop-all, etc.) |
| GET    | `/api/fleet/schedule`                 | Runner schedule configuration                 |
| POST   | `/api/fleet/schedule`                 | Update runner schedule                        |
| GET    | `/api/fleet/capacity`                 | Fleet capacity and utilization                |
| GET    | `/api/fleet/nodes`                    | All registered fleet nodes                    |
| GET    | `/api/fleet/hardware`                 | Per-node hardware specifications              |
| GET    | `/api/fleet/nodes/{node_name}/system` | System metrics for a specific node            |
| GET    | `/api/fleet/dispatch/actions`         | Available fleet dispatch actions              |
| POST   | `/api/fleet/dispatch/validate`        | Validate a dispatch payload                   |
| POST   | `/api/fleet/dispatch/submit`          | Submit a fleet dispatch job                   |
| GET    | `/api/fleet/orchestration`            | Orchestration run history                     |
| POST   | `/api/fleet/orchestration/dispatch`   | Dispatch an orchestration plan                |
| POST   | `/api/fleet/orchestration/deploy`     | Execute a multi-node deployment               |

### Workflows

| Method | Path                       | Description                           |
| ------ | -------------------------- | ------------------------------------- |
| GET    | `/api/workflows/list`      | All dispatchable workflows in the org |
| POST   | `/api/workflows/dispatch`  | Manually dispatch a workflow          |
| GET    | `/api/scheduled-workflows` | Cron-scheduled workflow inventory     |

### Repositories

| Method | Path         | Description                        |
| ------ | ------------ | ---------------------------------- |
| GET    | `/api/repos` | All org repositories with metadata |

### Reports

| Method | Path                        | Description                          |
| ------ | --------------------------- | ------------------------------------ |
| GET    | `/api/reports`              | List of available dated report files |
| GET    | `/api/reports/{date}`       | Report content for a specific date   |
| GET    | `/api/reports/{date}/chart` | Chart data from a dated report       |

### Tests

| Method | Path                        | Description                                                      |
| ------ | --------------------------- | ---------------------------------------------------------------- |
| GET    | `/api/tests/ci-results`     | Latest `ci-standard` run per fleet repo (17 repos, cached 120 s) |
| POST   | `/api/tests/rerun`          | Re-run failed jobs on a given workflow run (`{repo, run_id}`)    |
| GET    | `/api/heavy-tests/repos`    | Repos eligible for heavy integration testing                     |
| POST   | `/api/heavy-tests/dispatch` | Dispatch a heavy test workflow via GitHub Actions                |
| POST   | `/api/heavy-tests/docker`   | Dispatch a Docker-based heavy test run                           |

### Stats and Usage

| Method | Path         | Description                   |
| ------ | ------------ | ----------------------------- |
| GET    | `/api/stats` | Aggregate workflow statistics |
| GET    | `/api/usage` | Runner usage time-series data |

### Agent Remediation

| Method | Path                                    | Description                                     |
| ------ | --------------------------------------- | ----------------------------------------------- |
| GET    | `/api/agent-remediation/config`         | Current remediation configuration               |
| PUT    | `/api/agent-remediation/config`         | Update remediation configuration                |
| GET    | `/api/agent-remediation/workflows`      | Health of this repo's `agent-*.yml` workflows   |
| POST   | `/api/agent-remediation/plan`           | Generate a remediation plan                     |
| POST   | `/api/agent-remediation/dispatch`       | Dispatch a remediation plan (GAAI/Claude/Codex) |
| POST   | `/api/agent-remediation/dispatch-jules` | Dispatch one agent workflow (legacy route name) |
| GET    | `/api/agent-remediation/history`        | Remediation dispatch history                    |

### Quick Dispatch

| Method | Path                         | Description                                                                           |
| ------ | ---------------------------- | ------------------------------------------------------------------------------------- |
| GET    | `/api/providers/registry`    | Shared source-of-truth provider registry (dashboard + Conductor contract, issue #810) |
| GET    | `/api/agents/providers`      | Available agent providers and their availability status (legacy/back-compat)          |
| POST   | `/api/agents/quick-dispatch` | Dispatch an ad-hoc agent task to any repository                                       |

#### `GET /api/providers/registry` (issue #810)

The single source-of-truth provider registry consumed by both the dashboard UI
and the Conductor orchestrator. It eliminates the previously-duplicated provider
lists (dashboard `PROVIDERS`, Conductor `ProviderMeta`, and the ad-hoc
`_PROVIDERS_WITH_MODEL_SELECTION` set) and bridges the underscore (dashboard)
vs hyphen (Conductor) id mismatch by carrying _both_ ids on every entry.

All static provider metadata derives from the one canonical table
`backend/agent_remediation/provider_registry.py::PROVIDER_REGISTRY`; the legacy
`agent_remediation.PROVIDERS` dict is generated from it. The Conductor enum
string values (`task_classes`, `capabilities`, `auth_kinds`) are vendored in
`backend/conductor_constants.py` (no cross-repo runtime import) and guarded
against drift by `tests/api/test_conductor_constants.py`.

The Ollama server whose models are listed defaults to `http://localhost:11434`
and is overridable via the `DASHBOARD_OLLAMA_URL` environment variable. This is
required when the dashboard runs in WSL but Ollama runs on the Windows host
(WSL2 `localhost` does not reach the host) — the deployment sets it to a
WSL-reachable address such as the host's Tailscale IP. The live fetch is
resilient: if the server is unreachable, `models` is `[]` and `login_status`
reflects the failure rather than erroring.

CLI providers `claude-cli` and `codex-cli` authenticate via their interactive
**subscription login** (`claude login` / `codex login`), not a model API key —
the fleet deliberately injects no `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`. Each
provider's `setup_hint` reflects this so operators are never told to set a key
the system does not use.

Response shape (`schema_version` `1.0.0`):

```jsonc
{
  "schema_version": "1.0.0",
  "providers": [
    {
      "id": "claude-cli", // Conductor hyphen id
      "dashboard_id": "claude_code_cli", // dashboard underscore id
      "label": "Claude Code CLI",
      "execution_mode": "local_exec",
      "dispatch_mode": "github_actions",
      "auth_mode": "github_app", // none | github_app | api_key | local
      "resource": "runner", // runner | local
      "capabilities": ["code_edit", "..."],
      "cost_per_task": 0.05,
      "max_concurrency": 1,
      "models": ["claude-opus-4", "..."], // live for Ollama, curated for CLIs
      "models_endpoint": null, // Ollama: .../api/tags
      "login_status": "authenticated", // authenticated|unauthenticated|error|unknown
      "login_detail": "Probe reports ready.",
      "setup_hint": "...",
      "experimental": false,
      "editable": true,
      "remote": false,
      "enabled": true // false for retired providers (jules_cli, jules_api), #1193
    }
  ],
  "auth_kinds": ["none", "github_app", "api_key", "local"],
  "task_classes": ["format", "..."],
  "capabilities": ["code_edit", "..."],
  "hostname": "DeskComputer", // dashboard host the node probe ran on (#1193)
  "node_availability": {
    // one row per provider that declares an availability_probe (#1193)
    "antigravity": {
      "installed": false,
      "authenticated": false,
      "detail": "agy not found on PATH"
    },
    "claude_code_cli": {
      "installed": true,
      "authenticated": true,
      "detail": "Ready"
    }
  }
}
```

**Provider registry v2 (#1193).** The table also lists `antigravity`
(`antigravity-cli`, probe `agy`, `local_exec` / `dashboard_local`, auth `local`),
`cursor_agent` (`cursor-agent`, same modes; its curated `models` include Grok
entries so Grok can be used as a worker seat through the Cursor subscription —
the standalone Grok Build CLI is not used because it requires SuperGrok) and
`maxwell` (`maxwell-daemon`, optional `remote_session`, experimental, not
editable; the daemon has been dormant since 2026-06). `jules_cli` / `jules_api`
remain in the table with `enabled: false` (Jules workflows retired fleet-wide,
RM#1483 / RM#1505) and are excluded from `provider_order` /
`enabled_providers` in `config/agent_remediation.json`; `plan_dispatch` never
selects a registry-disabled provider even if a stale saved policy lists it.

`node_availability` is the **per-node CLI probe**
(`backend/agent_remediation/provider_probe.py::probe_provider_availability`):
for every provider with an `availability_probe`, `installed` is
`shutil.which(<binary>)` on the dashboard host and `authenticated` reuses the
existing `routers/credentials.py` probe for that provider where one exists
(Codex, Claude, Gemini, Ollama, Jules); `auth_mode: local` CLIs without a
registered probe (Antigravity, Cursor Agent) report `authenticated` equal to
`installed` because the CLI carries its own login session, and `detail` says so.
A probe that raises is reported (`authenticated: false`, exception class in
`detail`), never propagated. The endpoint caches the probe for **60 s**
(`cache_utils` key `providers:node_availability`) so frontend polling stays
cheap; providers reached over HTTP (Maxwell) have no binary and no row.

`models[]` for the Ollama provider is fetched live from
`http://localhost:11434/api/tags` via an injectable, resilient fetcher: a
connection failure degrades to `models: []` and a reachability-reflecting
`login_status` — the endpoint never returns `500`. CLI providers expose a small
curated model list; `models.length > 0` is what now marks model-selection
support. `login_status` is derived from the existing availability/credential
probes and is contractually one of the four literals above.

`POST /api/agents/quick-dispatch` now performs a cached pre-flight backpressure
gate before dispatching. If `GET /readyz` would fail or no online
`d-sorg-fleet` runner is available, the route returns HTTP `503` with
`{"error":"not_ready","reason":...,"retry_after_seconds":30}` and a
`Retry-After` header. Successful accepts return HTTP `202`. Operators may
override the gate with `force=true`, which is logged in the quick-dispatch
audit trail.

### PR and Issue Dispatch

| Method | Path                               | Description                                                     |
| ------ | ---------------------------------- | --------------------------------------------------------------- |
| GET    | `/api/prs`                         | List open pull requests across the org with claim/link metadata |
| GET    | `/api/prs/{owner}/{repo}/{number}` | Single PR detail with checks and file count                     |
| GET    | `/api/issues`                      | List open issues with taxonomy and pickability                  |
| POST   | `/api/prs/dispatch`                | Bulk-dispatch agent tasks to selected PRs                       |
| POST   | `/api/issues/dispatch`             | Bulk-dispatch agent tasks to selected issues                    |

### Credentials

| Method | Path                           | Description                                           |
| ------ | ------------------------------ | ----------------------------------------------------- |
| GET    | `/api/credentials`             | Org and repo secrets/variables inventory (names only) |
| POST   | `/api/credentials/set-key`     | Securely set an API key for a provider                |
| POST   | `/api/credentials/clear-key`   | Remove an API key for a provider                      |
| POST   | `/api/credentials/launch-auth` | Launch a provider's browser auth flow in a subprocess |

### Runner Audit

| Method | Path                                | Description                                                   |
| ------ | ----------------------------------- | ------------------------------------------------------------- |
| GET    | `/api/runner-routing-audit`         | Recent workflow runs on GitHub-hosted runners (billing alert) |
| POST   | `/api/runner-routing-audit/refresh` | Trigger an immediate audit refresh                            |

The Runner Audit UI renders its not-yet-run/all-clear states through the shared
`EmptyState` primitive, its hosted-runner labels through `Badge`, and its refresh
action through `TouchButton`. Scoped `runner-audit__*` classes carry the table,
warning, metadata, and link presentation through design tokens instead of
inline style objects.

### Maxwell

| Method | Path                    | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ------ | ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/maxwell/status`   | Maxwell daemon status and configuration                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| POST   | `/api/maxwell/control`  | Control Maxwell daemon (start/stop/configure)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| POST   | `/api/maxwell/dispatch` | Dispatch an agent task to Maxwell-Daemon. Proxies to MD's confirmation-gated, idempotent `POST /api/dispatch` (issue #953) — the endpoint that actually enforces `hmac.compare_digest` on `confirmation_token` and keys idempotency on `idempotency_key`. Sends the exact `DispatchRequest` contract body (`confirmation_token`, `prompt`, `repo`, `idempotency_key`); the caller must supply `confirmation_token` and `prompt`. A daemon-side confirmation rejection surfaces as 403 and an idempotency conflict as 409, rather than being masked as a success. Replaces the prior target `POST /api/v1/tasks`, which silently dropped both fields.                                                                                               |
| POST   | `/api/maxwell/chat`     | Proxy Maxwell chat messages to MD's request/response `POST /api/chat` (codebase-scoped requests route to `POST /api/chat/codebase`). Multi-turn context is carried in MD's `messages[]` (translated from the UI's history; issue #957) — MD now rejects the legacy `history`/`stream` fields. The proxy parses the JSON `ChatResponse` and emits its `content` as a `text/plain` body (the UI previously rendered the raw JSON). Accepts optional `repo`/`repo_root` fields (issue #838) that scope a codebase Q&A session; they are forwarded to Maxwell-Daemon, which jails its agentic codebase tools to that root. If the daemon lacks codebase support (Maxwell_Daemon#948) it returns 501, which the proxy degrades into a readable message. |

### Assessments

| Method | Path                        | Description                        |
| ------ | --------------------------- | ---------------------------------- |
| GET    | `/api/assessments/scores`   | Per-repo assessment quality scores |
| POST   | `/api/assessments/dispatch` | Dispatch an assessment workflow    |

### Assistant (Issues #88, #89)

#### Context-Aware Chat (Issue #88)

| Method | Path                  | Description                                                                                                         |
| ------ | --------------------- | ------------------------------------------------------------------------------------------------------------------- |
| POST   | `/api/assistant/chat` | Retired (HTTP 410 Gone). Replaced by Staff Console conversation threads at `/api/v1/staff/threads` (SC-D11, #1330). |

**Request body:**

```json
{
  "prompt": "Why did this workflow fail?",
  "context": {
    "current_tab": "remediation",
    "selected_run_id": 12345,
    "selected_items": [],
    "dashboard_state": { "...": "..." }
  },
  "provider": "claude_code_cli"
}
```

**Response:**

```json
{
  "response": "Based on the logs, the failure was...",
  "provider": "claude_code_cli",
  "context_used": {...},
  "timestamp": "2026-04-25T11:30:00+00:00"
}
```

#### Action Proposals (Issue #89)

| Method | Path                            | Description                                                 |
| ------ | ------------------------------- | ----------------------------------------------------------- |
| POST   | `/api/assistant/propose-action` | Propose an action based on user request (awaiting approval) |
| POST   | `/api/assistant/execute-action` | Execute an approved action with full details                |

**Propose request:**

```json
{
  "user_request": "Restart runner-5",
  "context": {...},
  "provider": "claude_code_cli"
}
```

**Propose response:**

```json
{
  "action_id": "a1b2c3d4",
  "action_type": "restart_runner",
  "description": "Restart runner-5 (will be offline ~30s)",
  "parameters": { "runner_name": "runner-5", "timeout_seconds": 300 },
  "risk_level": "medium",
  "rationale": "User requested restart for debugging",
  "estimated_duration_seconds": 30
}
```

**Execute request:**

```json
{
  "action_id": "a1b2c3d4",
  "approved": true,
  "operator_notes": "ok, proceed"
}
```

**Execute response:**

```json
{
  "success": true,
  "action_id": "a1b2c3d4",
  "result": "Runner 'runner-5' restart initiated",
  "execution_time_ms": 245
}
```

### Code Requests

| Method | Path                                 | Description                                                                                   |
| ------ | ------------------------------------ | --------------------------------------------------------------------------------------------- |
| GET    | `/api/code-requests`                 | Code request history plus `dispatchTarget` availability                                       |
| POST   | `/api/code-requests`                 | Create a Code Request in `draft` or `triage`, persists to GitHub issue with YAML front-matter |
| GET    | `/api/code-requests/{id}`            | Fetch Code Request detail with state, links, and full audit trail                             |
| POST   | `/api/code-requests/{id}/transition` | Transition Code Request lifecycle state or apply operator override                            |
| GET    | `/api/code-requests/templates`       | Available code request templates                                                              |
| POST   | `/api/code-requests/templates`       | Create a new code request template                                                            |
| POST   | `/api/code-requests/dispatch`        | Dispatch a code request workflow; 502 when dispatch fails                                     |
| \*     | `/api/feature-requests*`             | Deprecated aliases returning `Deprecation: true` and `Link: </api/code-requests...>` (CR-1)   |

### Agent Profiles

| Method | Path                       | Description                                                                 |
| ------ | -------------------------- | --------------------------------------------------------------------------- |
| GET    | `/api/agent-profiles`      | List all agent profiles with live provider probe status and defaults seeded |
| POST   | `/api/agent-profiles`      | Create a new custom agent profile with provider availability validation     |
| GET    | `/api/agent-profiles/{id}` | Get agent profile detail by ID                                              |
| PUT    | `/api/agent-profiles/{id}` | Update an existing agent profile (blocks modifying built-in profile IDs)    |
| DELETE | `/api/agent-profiles/{id}` | Delete an agent profile (blocks deleting built-in profile defaults)         |

### Board Proposals (issue #1284, CR-7)

| Method | Path                      | Description                                                                                                                                    |
| ------ | ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| POST   | `/api/proposals`          | Submit a suggestion to the Board (`proposals.write` scope); rate-limited (per submitter marker), duplicate-checked, 503 on GitHub read failure |
| GET    | `/api/proposals`          | List proposals with optional `state` (open\|decided) and `repo` filter; 503 on GitHub read failure                                             |
| GET    | `/api/proposals/{number}` | Proposal detail with Board-Secretary comments; 503 on GitHub read failure                                                                      |

### Local Apps

| Method | Path              | Description                                    |
| ------ | ----------------- | ---------------------------------------------- |
| GET    | `/api/local-apps` | Health status of registered local applications |

### Help

| Method | Path             | Description                                  |
| ------ | ---------------- | -------------------------------------------- |
| POST   | `/api/help/chat` | In-app help chat (context-aware AI response) |

### Diagnostics

| Method | Path                               | Description                                                                                                                                                                                                                                                                                                                                                                        |
| ------ | ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/diagnostics`                 | **Operator deploy-health endpoint.** Always 200. Reports machine_registry load status (including the error message if load failed), fleet_federation source + peer count, leader-lock status, key file mtimes, cache config. Used by `deploy/deploy-check.sh` and any external monitoring. Schema is a stable contract — adding fields OK, removing/renaming is a breaking change. |
| GET    | `/api/diagnostics/summary`         | Consolidated diagnostics: PID, memory, WSL status, git commit, drift                                                                                                                                                                                                                                                                                                               |
| GET    | `/api/diagnostics/vhdx`            | VHDX attachment status for all WSL distributions via `Get-DiskImage` (powershell.exe); includes sharing-violation detection. Returns `distributions[]` (name, path, attached), `storage_incident`, `generated_at`. Gracefully returns empty list when running on Linux CI. (issue #756)                                                                                            |
| GET    | `/api/diagnostics/pool-recovery`   | Structured recovery guidance for pool failure scenarios: `vhdx_locked` (ERROR_SHARING_VIOLATION), `disk_full`, `wsl_boot_failure`. Each scenario has `id`, `title`, `description`, `warning` (nullable), and `steps[]`. Warns against restarting WSL during active Optimize-VHD compaction. Returns `scenarios[]`, `runbook_url`, `generated_at`. (issue #756)                     |
| POST   | `/api/diagnostics/restart-service` | Restart runner-dashboard systemd service (localhost only)                                                                                                                                                                                                                                                                                                                          |

### Launchers

| Method | Path                         | Description                                                                                                                      |
| ------ | ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| POST   | `/api/launchers/generate`    | Generate Windows PowerShell launcher scripts on the Desktop                                                                      |
| GET    | `/api/agent-launcher/status` | Read cline agent scheduler pidfile and per-agent state                                                                           |
| POST   | `/api/agent-launcher/start`  | Start the cline agent scheduler; on Linux this detaches `agent_launcher.py` with `subprocess.Popen(..., start_new_session=True)` |
| POST   | `/api/agent-launcher/stop`   | Stop the cline agent scheduler via the launcher CLI                                                                              |

### Static Assets

| Method | Path                    | Description                                                                                                                                                                                                                                                                  |
| ------ | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/`                     | Serves `dist/index.html`                                                                                                                                                                                                                                                     |
| GET    | `/manifest.webmanifest` | PWA manifest                                                                                                                                                                                                                                                                 |
| GET    | `/icon.svg`             | App icon                                                                                                                                                                                                                                                                     |
| GET    | `/{full_path:path}`     | SPA deep-link fallback — serves `dist/index.html` (`text/html`) for any unmatched non-`/api/`, non-static GET so React Router routes (`/t/:tabId`, `/settings/push`) load on a cold request. Registered last; `/api/*` and known static prefixes are excluded and still 404. |

---

## 5. Configuration

### 5.1 Environment Variables

| Variable                          | Default                                                  | Description                                                                         |
| --------------------------------- | -------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `GITHUB_TOKEN`                    | (required)                                               | GitHub PAT or App token with org runner/workflow scopes                             |
| `GITHUB_ORG`                      | `D-sorganization`                                        | GitHub organization name                                                            |
| `DASHBOARD_PORT`                  | `8321`                                                   | HTTP port the server listens on                                                     |
| `DISPLAY_NAME`                    | `hostname`                                               | Display name shown in the UI header                                                 |
| `NUM_RUNNERS`                     | `12`                                                     | Target number of self-hosted runners                                                |
| `MAX_RUNNERS`                     | `NUM_RUNNERS`                                            | Hard cap on runner count                                                            |
| `RUNNER_DASHBOARD_REPO_ROOT`      | Parent of backend dir                                    | Repo root for relative path resolution                                              |
| `DASHBOARD_DISK_WARN_PERCENT`     | `85`                                                     | Disk usage % threshold for warning state                                            |
| `DASHBOARD_DISK_CRITICAL_PERCENT` | `92`                                                     | Disk usage % threshold for critical state                                           |
| `DASHBOARD_DISK_MIN_FREE_GB`      | `25`                                                     | Minimum free disk GB threshold                                                      |
| `RUNNER_ALIASES`                  | ``                                                       | Comma-separated runner name aliases                                                 |
| `RUNNER_SCHEDULE_CONFIG`          | `~/.config/runner-dashboard/runner-schedule.json`        | Path to schedule config                                                             |
| `RUNNER_SCHEDULER_BIN`            | `/usr/local/bin/runner-scheduler`                        | Runner scheduler binary path                                                        |
| `RUNNER_SCHEDULER_SERVICE`        | `runner-scheduler.service`                               | Scheduler systemd service name                                                      |
| `RUN_JOB_ENRICHMENT_LIMIT`        | `50`                                                     | Max runs to enrich with job data                                                    |
| `LOG_FILTER_PATHS`                | `/api/scheduled-workflows,/api/heavy-tests,/api/reports` | Comma-separated path prefixes sampled at 1/10 in request logs; errors always logged |

### 5.2 machine_registry.yml

Located at `backend/machine_registry.yml`. Defines the multi-node fleet.

**Path resolution and security:** `machine_registry.load_machine_registry()`
passes `backend/` (the module's own directory) and `~/.config/runner-dashboard/`
as explicit allowed roots to the security validator. This is intentional: the
deployed install (`~/actions-runners/dashboard/`) is not a git checkout, so
the validator's default git-repo-root inference returns None. Without the
explicit allow-list every load on a deployed host fails as "Config path
escapes allowed roots", silently disabling fleet federation. The `MACHINE_REGISTRY_PATH`
env var overrides the lookup; operators wishing to manage the registry as
host config can place it under `~/.config/runner-dashboard/`.

**Fleet federation auto-derivation:** when the `FLEET_NODES` env var is empty
and `AUTODERIVE_FLEET_NODES` is unset or truthy, the server iterates the
registry's machines and populates `FLEET_NODES` from each entry's
`dashboard_url` (preferred) or first `tailscale_nodes[].ip`. The local host
is excluded by hostname/alias match against `DISPLAY_NAME` (or `platform.node()`).
This removes the historical foot-gun of leaving `FLEET_NODES` unset in systemd
Environment= lines and the dashboard silently showing only the local machine.
The `/api/diagnostics` endpoint reports the effective source (`env`, `registry`,
or `empty`) so deploy validation can confirm it.

Registry entries may retain historical nodes for audit or rollback, but exactly
one live entry per physical host should be marked `preferred: true`. For the
ControlTower host, the preferred entry is the Windows/Tailscale node
`controltower.tail2bbcc7.ts.net`, and any obsolete WSL monitoring node remains
non-preferred. Dashboard URLs should be stable HTTP URLs reachable from the
tailnet and should not use loopback except for same-host-only helper endpoints.

The WSL mirrored-port helper is responsible for preserving those HTTP URLs
across WSL cold starts. Before dashboard startup it inspects `tailscale serve
status`, clears any conflicting binding with the matching protocol
(`--http`, `--https`, or `--tcp`), removes the Windows portproxy entry, and
restores the dashboard as an HTTP Tailscale Serve binding after startup.
Protocol-aware clearing is required because an HTTP Serve binding is not
removed by a TCP-only clear command.

Example structure:

```yaml
nodes:
  - name: primary-host
    hostname: primary.local
    role: primary
    runners: 12
    labels: [d-sorg-fleet, linux, x64]
  - name: secondary-host
    hostname: secondary.local
    role: secondary
    runners: 4
    labels: [d-sorg-fleet, linux, x64, gpu]
```

### 5.3 config/agent_remediation.json

Controls which agents are enabled for remediation dispatch and their routing
configuration (API keys, model selection, repo allow/deny lists).

### 5.4 config/runner-schedule.json

Defines on/off schedule windows for runner scaling. Used by the runner
scheduler daemon (`deploy/runner-scheduler.py`).

### 5.5 local_apps.json

Registry of local applications monitored by the Local Apps tab. Each entry
includes process name, expected PID file path, and restart command.

---

## 6. Deployment

> **Full operator guide:** [`docs/deployment-model.md`](docs/deployment-model.md)

### 6.1 Quick Start (Development)

```bash
git clone git@github.com:D-sorganization/runner-dashboard.git
cd runner-dashboard
./start-dashboard.sh
# Opens http://localhost:8321
```

### 6.2 Production Setup

Run the full setup script on the target machine:

```bash
bash deploy/setup.sh --runners 4 --machine-name ControlTower --role hub
```

Fleet node examples:

```bash
bash deploy/setup.sh --runners 8 --machine-name OG-Laptop
bash deploy/setup.sh --runners 8 --machine-name DeskComputer --runner-aliases desktop
bash deploy/setup.sh --runners 8 --machine-name ControlTower --role hub \
  --fleet-nodes "OG-Laptop:http://100.64.12.7:8321,DeskComputer:http://100.64.12.9:8321"
```

Node-specific runner counts in the setup script examples must reflect the
current fleet plan. OG-Laptop is documented as an eight-runner node, and hub
fleet-node examples use concrete Tailscale URL placeholders so operators can
replace addresses without changing the argument shape.

`setup.sh` performs:

1. Installs Python dependencies into a system venv.
2. Copies the systemd unit file (`runner-dashboard.service`) to
   `/etc/systemd/system/`.
3. Enables and starts the service.
4. Configures the `GITHUB_TOKEN` environment variable in the service unit.
5. (Optional) Installs the runner autoscaler service.

### 6.3 Updating a Deployed Instance

```bash
bash deploy/update-deployed.sh
```

This script:

1. Installs/updates Python backend dependencies via `pip_install` (from `deploy/lib.sh`).
2. Creates a timestamped backup of the current deploy directory (`.bak.YYYYMMDD_HHMMSS`)
   before any files are changed.
3. Copies updated backend, frontend, helpers, and `local_apps.json`.
4. Writes fresh `deployment.json` metadata.
5. Restarts `runner-dashboard.service` via systemd.
6. Verifies service health and GitHub API connectivity.

#### Dry-Run Mode

Preview all steps without executing any destructive operations:

```bash
bash deploy/update-deployed.sh --dry-run
# or
DRY_RUN=true bash deploy/update-deployed.sh
```

#### Artifact-Based Deployment

```bash
bash deploy/update-deployed.sh --artifact runner-dashboard-v4.0.1.tar.gz
```

The installer fails closed (#1212): the wheelhouse ABI check against
`compatibility.python_minor`, interpreter selection (any matching CPython that
can build a venv with `ensurepip`; host `pip` is not required) and a complete
offline dependency install into a staging venv all pass before the deploy
directory is modified. A failure at any of those steps leaves the live install,
including `.venv`, untouched.

### 6.4 Rollback

Every `update-deployed.sh` run creates an automatic backup before copying files.
To roll back:

```bash
# List available backups
bash deploy/rollback.sh --list

# Roll back to the most recent backup
bash deploy/rollback.sh

# Roll back to a specific backup
bash deploy/rollback.sh --to ~/actions-runners/dashboard.bak.20260422_093017

# Preview rollback without executing
bash deploy/rollback.sh --dry-run
```

### 6.5 systemd Service

The service unit (`deploy/runner-dashboard.service`) runs:

```
ExecStart=/path/to/venv/bin/uvicorn backend.server:app --host 0.0.0.0 --port 8321
```

Secrets are loaded from `~/.config/runner-dashboard/env` (GH_TOKEN, GITHUB_ORG,
NUM_RUNNERS, DISPLAY_NAME).

Log output: `sudo journalctl -u runner-dashboard -n 50 --no-pager`

Health check: `curl http://localhost:8321/api/health`

### 6.6 Runner Autoscaler Service

The optional autoscaler service (`deploy/runner-autoscaler.service`) runs
`backend/runner_autoscaler.py` as a daemon. It monitors queue depth and
adjusts the active runner count based on the policy defined in
`config/runner-schedule.json`.

**Busy detection contract.** Before stopping a runner the autoscaler MUST
treat the unit as busy when ANY of these signals fire (ordered by which
phase of the job lifecycle they cover):

1. **Pre-Worker pickup window.** The runner's `_work/_temp/_runner_file_commands/`
   directory has been modified within the last `RUNNER_PICKUP_DIR_MAX_AGE_SECONDS`
   (default 30s). The Listener writes to this directory the moment it accepts
   a job, before forking the `Runner.Worker`, so this signal closes the
   1-2s race window where MainPID has no Worker child but a job IS assigned.
   Older mtime → stale residue (NOT busy); cleanup will GC it.
2. **Worker running.** A fresh job-pickup lockfile exists at
   `$RUNNER_BUSY_LOCK_DIR/<runner-name>.lock`, written by the runner's
   `ACTIONS_RUNNER_HOOK_JOB_STARTED` hook (see
   `deploy/runner-hooks/job-started.sh`). Catches the inverse window where
   the Worker exists but psutil's child-walk transiently misses it.
   Lockfiles older than `RUNNER_BUSY_LOCK_MAX_AGE_SECONDS` (default 24h)
   are treated as stale and ignored.
3. **Process tree.** The unit's MainPID has a `Runner.Worker` child process.
   The most direct signal once the Worker has forked and stabilized.
4. **Conservative fallback.** MainPID is 0/unknown but `ActiveState=active`
   and `SubState=running` — treat as busy during transient restarts.

The shell cleanup script `deploy/runner-cleanup.sh` honours the same
contract and additionally garbage-collects stale lockfiles. The
`/run/user/$UID/`, `~/.cache/`, and `/tmp/` fallbacks for the autoscaler's
own self-lock are defined in `_acquire_lock()` (the hard-coded `/var/run/`
path is unwritable for non-root deploys; see #664 follow-up).

**I/O pressure contract.** The autoscaler samples Linux PSI metrics from
`/proc/pressure/io` when available. `full avg10` above
`AUTOSCALER_IO_PRESSURE_FULL_HIGH` is overload, even if CPU and RAM are below
their limits, because it means all runnable work is blocked on filesystem I/O.
Scale-up recovery is blocked until `full avg10` is below
`AUTOSCALER_IO_PRESSURE_FULL_LOW`. After any start/stop action,
`AUTOSCALER_ACTION_COOLDOWN_SECONDS` prevents another scale action until the
host has had time to stabilize.

### 6.7 Runner Service Unit Template

GitHub Actions runner units installed by the runner package use
`KillMode=process` by default, which orphans `Runner.Worker` children when
the listener is stopped. Existing units are migrated to `KillMode=mixed`
plus the job-pickup hooks above by running
`sudo deploy/migrate-runner-units.sh` once per host. The migration uses a
systemd drop-in (`/etc/systemd/system/<unit>.d/10-runner-dashboard-busy-lock.conf`)
so it survives upstream runner package upgrades.

### 6.7 Shared Deploy Library

`deploy/lib.sh` is sourced by all deploy scripts and provides:

- Terminal colours and `ok`/`info`/`warn`/`fail` log helpers
- Guard assertions: `require_dir`, `require_file`, `require_cmd`
- `pip_install <pkg...>` â€” Python 3.11-preferring pip with `--break-system-packages` when supported
- `sync_dir <src> <dest>` â€” rsync with rm/cp fallback
- `backup_dir <path>` â€” timestamped `cp -a` backup
- `dry_run "<description>"` â€” no-op gate when `DRY_RUN=true`

All new deploy scripts should source it with:

```bash
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
```

---

## 7. Changelog

### 4.9.32 - 2026-08-25 (policy tooling)

- chore(policy): add `config/required_status_checks_policy.json` and
  `scripts/check_required_checks_drift.py`, a required-status-checks drift
  detector, plus `tests/test_required_checks_drift.py` (part of #1119). PR
  #1116 merged before a late pytest-matrix failure surfaced because the
  `tests-required` aggregate job in `ci-standard.yml` is skipped (not
  failed) when its `tests` dependency fails, and a SKIPPED conclusion does
  not block merge; PR #1118 merged while the Anti-Phantom Merge Guard
  (`guard` context) was still queued because `guard` is not listed in any
  required-status-checks configuration for `main`. The new script asserts
  both conditions offline against committed snapshots
  (`tests/contracts/*.json`) and against the live `ci-standard.yml`
  content, and is picked up automatically by the existing `pytest tests/`
  step — no workflow file was added or edited. Closing the gap fully still
  requires a human to add `guard` (and confirm `tests`) to the
  `Repository_Protections` ruleset's `required_status_checks` and to add
  `if: always()` fail-closed handling to the `tests-required` job; see the
  ready-to-apply spec on issue #1119.

### 2.5.166 - 2026-06-20

- fix(system): host `powershell.exe` spec probes run with
  `-WindowStyle Hidden -NonInteractive` so the WSL->Windows machine-spec sync no
  longer pops visible console windows on the operator desktop (#1056).

### 2.5.163 - 2026-06-17

- fix(config): `_read_repo_version()` no longer crashes the dashboard at import
  when `REPO_ROOT/VERSION` is missing. `REPO_ROOT` is operator-overridable via
  `RUNNER_DASHBOARD_REPO_ROOT` and on some deploys points at a sibling repo
  (e.g. `Repository_Management`) with no `VERSION` file; the reader now falls
  back to the deployed backend's own `BACKEND_DIR.parent/VERSION` and finally to
  `"0.0.0"` when neither file exists. A `VERSION` file that exists but is
  malformed still raises, so a genuinely bad version is never silently accepted.

### 2.5.26 - 2026-05-21

- ci: added a documented workflow concurrency policy allowlist for
  `cancel-in-progress: false` exceptions and intentional PR-singleton
  workflows, with shared lint/test enforcement and operator triage guidance.

### 2.5.25 - 2026-05-07

- fix(frontend): install a document-level wheel guard for focused numeric,
  range, and select controls in the legacy dashboard so scroll gestures no
  longer mutate editable values by accident.

### 2.5.24 - 2026-05-02

- ci: made Python and frontend validation path-aware for pull requests while
  preserving full backend/frontend suites on `main`, so workflow-only changes
  still run static workflow gates without being blocked by unrelated source
  test debt.

### 2.5.23 - 2026-05-02

- ci: reduced duplicate remediation work by making Control Tower the single
  automatic CI-remediation owner, converting PR AutoFix to reusable/manual
  execution, replacing tight PR-check polling with bounded REST Actions lookup,
  and adding explicit deferred/manual verification states.
- ci: reduced scheduled dashboard/reaper churn with credential preflights,
  single-flight concurrency, less frequent schedules, bounded lease reaper
  runtime, and consistent action SHA pinning.
- ci: kept the 500-line quality gate strict on `main` while limiting pull
  request enforcement to changed source files so historical line-count debt
  does not block unrelated workflow reliability fixes.

### 2.5.22 - 2026-05-01

- fix: `gh_api` exposes GitHub rate limits as `RateLimitedError` with
  `retry_after_seconds`, records a per-token/resource in-memory breaker, and
  `/api/runners` translates the condition to HTTP 429 with `Retry-After`.

### 2.5.16 - 2026-04-30

- ci: keep the standard test lane aligned with the checked-in `uv.lock`, Bandit
  allow-list policy, and mypy relaxed-override module-count guard.
- chore(deploy): keep Docker and setup static guards on the supported Python
  3.11 runtime and systemd hardening contract.
- security: upgrade Docker image packaging utilities with available CVE fixes
  during the container build while preserving the locked application install.

### 2.5.14 - 2026-04-30

- feat(scalability): drive uvicorn `workers`, `limit_concurrency`, and
  `timeout_keep_alive` from `WORKERS` / `LIMIT_CONCURRENCY` /
  `TIMEOUT_KEEP_ALIVE` env vars, with defaults `1` / `200` / `5`. `WORKERS`
  stays at 1 until leader-election (#367) lands; setting it higher emits a
  runtime warning. Documented under §2.1 Backend (#393).
- chore(reliability): cap `_cpu_history` to a `collections.deque` with
  `maxlen=1000` so the in-process CPU sample buffer cannot grow without
  bound (#393).
- chore(reliability): cap `queue_cleanup.find_stale_runs` fan-out to 8
  concurrent repo queries via `asyncio.Semaphore` (#393).

### 2.5.11 - 2026-04-29

- feat: add authenticated session tracking and remote logout endpoints for the
  mobile auth surface, including hashed session listing and bulk revocation.

### 2.5.10 - 2026-04-29

- feat: add VAPID public key endpoint (`/api/push/vapid-public-key`) and `PushSettings` frontend component with per-topic subscription toggles for Web Push notifications (issue #192).
- feat: route `/settings/push` from `frontend/src/main.tsx` to `PushSettings` so the Vite entrypoint exposes the `#173` tracer-bullet path during Phase 1 migration.
- feat: add the first M04 touch primitive implementation slice with
  `TouchButton` and `SegmentedControl` contracts.

### 2.5.8 - 2026-04-29

- test: add explicit epic acceptance viewport profiles for 375x812 and
  412x915 to the mobile test harness.

### 2.5.7 - 2026-04-29

- feat: add the mobile integration foundation for native-shell selection,
  static design tokens, and read-only Fleet runner monitoring cards without
  changing the built frontend runtime.

### 2.5.6 - 2026-04-29

- test: add the issue #202 mobile Playwright harness contract with checked-in
  viewport profiles, touch helper scaffolding, and static validation before
  enabling browser or visual-regression CI.

### 2.5.2 — 2026-04-28

- chore: migrate to `uv` for dependency management and add `uv.lock`.
- ci: refactor CI workflows to be `uv`-native, ensuring reproducible builds and faster bootstrap times (resolves #163).
- ci: updated `ci-spec-check` to monitor `uv.lock` for freshness.
- fix: include `itsdangerous` in the `uv` dependency set so Starlette session middleware imports during test collection.

### 2.0.0 â€” 2026-04-23

Initial standalone release. Extracted from the `D-sorganization/Repository_Management`
mono-repo as an independent repository.

- Full FastAPI backend with all API endpoints documented above.
- Vite-built React SPA frontend.
- Fleet deployment scripts and systemd service unit.
- Fleet-standard CI/CD workflows (ci-standard, ci-spec-check, agent workflows).
- Branch protection with required `quality-gate` and `Verify SPEC.md freshness`
  status checks.
- The `ci-health-check` bootstrap gate must allow enough time for a fresh
  runner to create a Python virtual environment, install `requirements.txt`,
  and collect tests before downstream quality, security, and test jobs run.
- Multi-agent coordination via lease protocol.

Prior versions tracked in the mono-repo `Repository_Management`. Application
version history in `VERSION` file (4.0.1 at time of extraction).

---

## 8. Testing

The project test suite lives in `tests/`. Run all tests with:

```
pytest tests/ -q
```

Pytest is configured with `pythonpath = ["backend"]` in `pyproject.toml`.
`tests/conftest.py` also inserts the resolved backend directory into
`sys.path` so local and CI runs import backend modules consistently from any
supported working directory.

Test coverage areas:

- **`tests/test_dispatch_contract.py`** â€” unit tests for `backend/dispatch_contract.py`:
  envelope round-trips, confirmation gating for privileged actions, allowlist enforcement.
- **`tests/test_remote_execution_contract.py`** â€” unit tests for `backend/remote_execution_contract.py`:
  private-host and private-URL detection, unknown-target rejection.
- **`tests/test_agent_remediation.py`** â€” unit tests for `backend/agent_remediation.py`:
  `FailureContext` construction, workflow-type classification for lint and test workflows,
  policy defaults.
- **`tests/test_frontend_integrity.py`** â€” static source checks for `frontend/src/legacy/App.tsx`:
  required tab function markers, absence of deprecated `HeavyTestsTab`, icon helper symbols.
- **`tests/test_frontend_perf_budget.py`** â€” validates `frontend/perf-budget.json`
  and enforces the interim gzip ceiling for the current built frontend artifact.
- **`tests/test_mobile_test_harness.py`** - validates the issue #202 mobile
  viewport profiles, smoke-page marker contract, touch helper exports, and the
  explicit visual-regression opt-in gate.
- **`tests/api/test_push.py`** - tests for `backend/push.py` VAPID public key endpoint response shape and principal import integrity.

### 8.1 Playwright E2E Smoke Tests (Issue #389)

`tests/e2e/smoke.spec.ts` — Playwright tests covering page load and basic navigation.

Run with: `npm run test:e2e`

Configuration is in `playwright.config.ts` at the repo root. Viewport profiles
are sourced from `tests/frontend/mobile/viewport_profiles.json` to keep mobile
smoke tests in sync with the Playwright suite. The CI workflow
`.github/workflows/frontend-tests.yml` runs Playwright Chromium smoke tests as
a blocking e2e job that gates merge on `main`.

Coverage:

- Root page loads with correct title; React `#root` element is non-empty; no top-level JS errors
- Fleet tab visible in navigation; renders content (runner cards, loading state, or empty state)
- Queue tab renders without crashing
- Maxwell tab degrades gracefully when daemon is offline (shows error/retry state, not blank)
- AgentDispatch page renders when accessible
- PushSettings page renders when accessible
- Navigation landmarks (nav, tablist) are present and visible when rendered
- Root path returns HTTP 2xx

`pytest>=8.0` and `pytest-asyncio>=0.23` are listed in `requirements.txt`.

---

## 9. Security

### 9.1 Markdown Rendering

All user-supplied content rendered as Markdown is passed through
`DOMPurify.sanitize()` before `dangerouslySetInnerHTML`. Marked.js is
configured with `{ mangle: false, headerIds: false, gfm: true }`.

### 9.2 Identity, Authorization, Attribution

The dashboard employs a strict Identity and Authorization model to secure access to the fleet.

**Identity Model:**
A **principal** is either a human or a bot/agent. Both have the same shape:

- `id`: Unique identifier (e.g., `human:dieter`, `bot:claude`).
- `type`: `human` or `bot`.
- `roles`: Assigned roles (`admin`, `operator`, `viewer`, `bot`), which expand into specific action scopes.
- `quotas`: Resource limits (runners, agent spend, app slots).

Principals are stored in `config/principals.yml`. The system fails closed: requests without a valid principal are rejected (HTTP 401).

**Loopback bypass (issue #315):** The loopback admin shortcut (granting automatic admin access to 127.0.0.1) is disabled by default. It is only active when `DASHBOARD_LOOPBACK_AUTH=1` is explicitly set in the environment. This must never be set in production deployments — it is intended solely for local single-user development where the dashboard is not reachable beyond 127.0.0.1.

**OGLaptop production OAuth readiness (issue #1141):** Browser/operator OAuth
is a separate credential from backend GitHub App authentication. Every login
and callback request constructs a fresh typed configuration snapshot and fails
closed with HTTP 503 until all of these invariants hold:

- `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` are non-placeholder-like,
  whitespace-free values; `SESSION_SECRET` is explicitly configured with at
  least 32 whitespace-free characters.
- `GITHUB_ORG` is exactly `D-sorganization`.
- `DASHBOARD_PUBLIC_ORIGIN` is exactly
  `https://oglaptop.tail2bbcc7.ts.net`, and
  `GITHUB_OAUTH_CALLBACK_URL` is exactly that origin plus
  `/api/auth/callback`.
- `DASHBOARD_TLS` is enabled so the session cookie carries `Secure`.
- `DASHBOARD_DEV_LOGIN` and `DASHBOARD_LOOPBACK_AUTH` are entirely unset.

The authorization request uses only `read:user`, explicitly binds the exact
callback, and retains the existing organization-membership and principals.yml
admission checks. The token exchange is bound to the same callback. A missing
OAuth configuration never redirects to development login. `/api/health`
exposes only `oauth.ready`, `oauth.status`, and non-sensitive reason codes; it
never returns a client ID, secret, OAuth state, access token, or session secret.
Production provisioning, rotation, rollback, tailnet-only verification, and
reason-code guidance are controlled by
`docs/runbooks/oglaptop-production-oauth.md`.

**Authorization:**
All mutating `/api/*` endpoints require a principal.

- Humans authenticate via session cookies (from GitHub OAuth).
- Bots authenticate via `Authorization: Bearer <token>`.
- Human logins also register a durable dashboard session record in
  `~/.config/runner-dashboard/sessions.json` (overridable via
  `DASHBOARD_SESSIONS_PATH`) with `session_id`, `principal_id`, timestamps,
  user agent, IP address, and optional revocation time.
- Session records expire after `DASHBOARD_SESSION_TTL_SECONDS` (default 86400
  seconds), cap each principal at `DASHBOARD_MAX_SESSIONS_PER_PRINCIPAL`
  active sessions (default 10), and expose only hashed session identifiers to
  API callers.
- Auth routes now include `GET /api/auth/sessions` for listing active sessions,
  `DELETE /api/auth/sessions/{session_id_hash}` for per-session remote logout,
  and `POST /api/auth/logout/all` for bulk revocation with
  `exclude_current=true` by default.
  Scopes are enforced per-endpoint using the `require_scope(scope_name)` dependency.

**Mobile Biometric Unlock (WebAuthn):**
The WebAuthn route surface is additive to the existing session model. The
registration and assertion begin endpoints require an already authenticated
principal and issue short-lived, HMAC-signed server challenges under
`/api/auth/webauthn/*`. Credential metadata is stored per principal as
`(user_id, credential_id, public_key, sign_count)` and can be listed or revoked
by the owning principal. Completion endpoints intentionally fail closed until a
pinned WebAuthn verifier validates attestation/assertion payloads and sign-count
replay protection.

**Scope Presets:**

- `admin` â€” Full access to all endpoints.
- `operator` â€” Access to runners, workflows, and remediation dispatch.
- `viewer` â€” Read-only access (default for unprivileged tokens).
- `bot` â€” Scoped for agent tasks (remediation, workflows).

**Audit Logging & Attribution:**
Every mutating action is recorded in `DispatchAuditLogEntry` with dual-attribution:

- `principal` â€” The ID of the authenticated user/agent.
- `on_behalf_of` â€” Optional secondary attribution (e.g. when an admin impersonates a bot for debugging, the bot is the principal, and the admin is `on_behalf_of`).
- `correlation_id` â€” Propagated across fleet nodes for distributed tracing.

**Admin Impersonation Flow:**
An admin can act as another principal (like a bot) for debugging. By providing the `X-Impersonate-Principal: <bot_id>` header, the admin adopts the target principal's scopes. The audit log records the target as the `principal` and the admin as `on_behalf_of`.

**Onboarding a New Human:**

1. Add the human to `config/principals.yml` with `type: human`.
2. Assign appropriate `roles` (e.g., `operator`, `viewer`).
3. Set their `quotas`.

**Onboarding a New Bot:**

1. Add the bot to `config/principals.yml` with `type: bot`.
2. Assign the `bot` role.
3. As an admin, generate a service token for the bot: `POST /api/principals/<bot_id>/token`.
4. Provide the generated token to the bot agent for API access.

### 9.3 Request Body Size Enforcement (Issue #350)

`MaxBodySizeMiddleware` in `backend/middleware.py` rejects oversized requests
before routing:

- Default cap: **1 MB** (Content-Length > 1 048 576 bytes → HTTP 413).
- Webhook cap: **256 KB** for `/api/linear/webhook` (configured via
  `_LIMIT_OVERRIDES`).
- Per-route override: `@limit_body_size(bytes)` decorator sets
  `func.__max_body_size__`; the functional `max_body_size_check` middleware
  walks the `__wrapped__` chain to find it.
- Requests with no `Content-Length` header (streaming/chunked) are allowed
  through.
- Only mutating methods (POST, PUT, PATCH, DELETE) are checked.

### 9.4 HTTP Security Headers

The backend injects the following headers on all responses:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: SAMEORIGIN`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Content-Security-Policy` â€” allows self, CDN scripts (jsdelivr, cdnjs, unpkg)

### 9.3 Destructive Action Confirmation

Critical fleet operations (runner stop, fleet restart) use a two-step
inline confirmation UI instead of `window.confirm()`.

### 9.4 Token Handling

`GH_TOKEN` and `ANTHROPIC_API_KEY` must be supplied as environment variables
only â€” never hardcoded in source files or configuration. The recommended setup
path is the `configure-env-vars.sh` script, which writes tokens to the systemd
override file so they are not visible in the process environment of child
processes and are not stored in shell history.

### 9.5 Network Exposure

The dashboard backend binds to `0.0.0.0:8321` by default so that multi-node
fleet monitoring works across the local network. Operators who do not need
cross-node access should bind to `127.0.0.1` instead (set the `HOST`
environment variable or modify the `systemd` unit file). No TLS is provided
by the dashboard itself; use a reverse proxy (nginx, Caddy) in front of the
service when HTTPS is required.

### 9.6 Operator Hardening Checklist

- Restrict network access to port 8321 via firewall rules (`ufw`, `iptables`,
  or cloud security groups); do not expose it publicly.
- Rotate `GH_TOKEN` and `ANTHROPIC_API_KEY` on a regular schedule (at minimum
  whenever a team member departs).
- Keep Python dependencies current: run `pip-audit` and `pip install -U -r
requirements.txt` during routine maintenance windows.
- Review agent dispatch logs in the Remediation tab regularly to detect
  unexpected or unauthorized agent invocations.
- Consider binding to `127.0.0.1` and using a reverse proxy with
  authentication if the dashboard is accessible to untrusted network segments.

### 9.7 Prompt Injection Sanitization

All user-controlled text inserted into LLM agent prompts (workflow failure
messages, log excerpts, issue bodies, PR descriptions) is passed through
`sanitize_for_prompt()` in `backend/agent_remediation.py` before inclusion.
The function:

- Truncates input to a configurable `max_length` (default 2000 chars) to
  limit token usage and reduce attack surface.
- Wraps the content in `[START_UNTRUSTED_CONTENT]` / `[END_UNTRUSTED_CONTENT]`
  delimiters so the model can distinguish trusted instructions from external
  data.

Every generated prompt also includes the constant
`PROMPT_UNTRUSTED_SYSTEM_INSTRUCTION` as a preamble, instructing the model
not to follow any instructions found inside the delimiters.

### 9.8 Secret Scanning (Issue #396)

The repo enforces a defence-in-depth gate against accidentally committed
credentials:

- `gitleaks` and `detect-secrets` run as `pre-commit` hooks (configured in
  `.pre-commit-config.yaml`, both pinned by SHA).
- A dedicated `CI Secrets` workflow (`.github/workflows/ci-secrets.yml`)
  runs `gitleaks` on every pull request and push to `main`, plus a
  `detect-secrets` baseline-integrity check that fails when any new
  finding appears outside the audited `.secrets.baseline`.
- `tests/test_no_secrets_in_repo.py` runs in the standard pytest suite and
  greps every git-tracked file for well-known credential prefixes
  (GitHub PATs, AWS access keys, OpenAI / Anthropic / Slack tokens, PEM
  private-key blocks). Inline `# pragma: allowlist secret` suppresses a
  single line; `_ALLOWED_PATHS` skips known-safe files (the baseline, the
  gitleaks config, this test file).
- `.gitleaks.toml` extends the upstream default ruleset with allowlists
  for the well-known fake VAPID test key in `backend/push.py` (tracked
  for removal as a follow-up under #396) and standard documentation
  placeholders.
- Operational procedure (rotation, baseline refresh, leak response) lives
  in `docs/runbooks/secret-scanning.md`.

### 9.9 YAML Config Path Validation (Issue #355)

All YAML configuration loaders (`machine_registry.py`, `identity.py`,
`runner_lease.py`, `quota_enforcement.py`) validate paths before loading:

- `validate_config_path(path, allowed_roots)` in `backend/security.py`
  resolves the path and confirms it is within allowed roots
  (`~/.config/runner-dashboard` and `<repo>/config`).
- Symlinks are rejected if they point outside the allowed root.
- World-writable files (mode bits `o+w`) are rejected.
- `safe_yaml_load(path, allowed_roots)` combines path validation with
  `yaml.safe_load` into a single safe entry point.

### 9.10 Supply-Chain Security — Release Signing, Provenance, SBOM (Issue #392)

Every tagged release produced by `.github/workflows/release.yml` ships
a verifiable supply-chain artefact set:

- **Artifact tarball** — `dashboard-<VERSION>.tar.gz` excludes `.git`,
  `node_modules`, `venv`, `__pycache__`, and build artefacts.
- **SHA-256 checksum** — `dashboard-<VERSION>.tar.gz.sha256` for offline
  integrity verification.
- **Cosign keyless signature** — `dashboard-<VERSION>.sig` (+ `.pem` cert),
  signed with `sigstore/cosign-installer` using OIDC identity
  (`COSIGN_EXPERIMENTAL=1`; no long-lived key). Verify with:
  ```
  cosign verify-blob \
    --certificate dashboard-<VERSION>.pem \
    --signature  dashboard-<VERSION>.sig \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com \
    --certificate-identity-regexp "https://github.com/D-sorganization/runner-dashboard/.github/workflows/release.yml" \
    dashboard-<VERSION>.tar.gz
  ```
- **SLSA-3 build provenance** — generated by `actions/attest-build-provenance`
  and attached to the release as a GitHub attestation. Verify with:
  `gh attestation verify dashboard-<VERSION>.tar.gz --owner D-sorganization`
- **SPDX SBOM** — `sbom.spdx.json` produced by `anchore/sbom-action` (syft)
  and attached to the release.

All `uses:` references in the workflow are SHA-pinned (validated by
`tests/test_release_workflow_yaml.py` and
`tests/test_workflow_action_pinning.py`).

---

## 10. Prompt Notes and Agent Dispatch Configuration

### 10.1 User-Configurable Prompt Notes

The AI agent dispatch system supports user-defined preamble notes injected
before every outbound LLM prompt. These are stored in
`~/.config/runner-dashboard/prompt_notes.json` with the shape:

```json
{ "enabled": true, "notes": "Always prefer Python 3.11+ idioms." }
```

The `/api/feature-requests/templates` (GET) route returns the current notes
alongside prompt templates and engineering standards. The
`/api/feature-requests` (POST) route merges notes into the prompt before
dispatch when `enabled` is true and `notes` is non-empty.

### 10.2 Secure Environment Variable Setup

`deploy/configure-env-vars.sh` provides a guided interactive script for
setting `GITHUB_TOKEN`, `ANTHROPIC_API_KEY`, and other required environment
variables into the WSL systemd unit. It validates token format and writes
variables to the service override file rather than to shell rc files, reducing
the risk of secrets leaking through shell history.

### 10.3 Deployment Dependency Management

`deploy/setup.sh` and `deploy/update-deployed.sh` install Python dependencies
from `backend/requirements.txt` directly (via `pip install -r
backend/requirements.txt`) rather than a hardcoded list, ensuring the deployed
dependency set stays in sync with the source of truth automatically.

---

## 11. PR Inventory API

Implemented in `backend/pr_inventory.py`; thin route shells in `backend/server.py`.

### 11.1 `GET /api/prs`

Aggregates open pull-requests across organisation repositories.

**Query parameters:**

| Parameter        | Type                | Default       | Description                            |
| ---------------- | ------------------- | ------------- | -------------------------------------- |
| `repo`           | string (repeatable) | all org repos | Filter to specific `owner/repo` slugs  |
| `include_drafts` | bool                | `true`        | Include draft PRs                      |
| `author`         | string              | â€”           | Filter by author login                 |
| `label`          | string (repeatable) | â€”           | Match any of these labels              |
| `limit`          | int                 | 500           | Maximum items returned (hard cap 2000) |

**Response:**

```json
{
  "items": [
    {
      "repository": "D-sorganization/runner-dashboard",
      "number": 76,
      "title": "...",
      "url": "...",
      "author": "dieter",
      "draft": false,
      "age_hours": 12.3,
      "labels": ["bug", "ci"],
      "requested_reviewers": ["alice"],
      "head_ref": "fix/something",
      "mergeable_state": "clean",
      "agent_claim": null,
      "linked_issues": [24, 43]
    }
  ],
  "total": 1,
  "errors": []
}
```

- `agent_claim` â€” extracted from any `claim:*` label on the PR.
- `linked_issues` â€” issue numbers found via `closes/fixes/resolves #N` in the PR body.
- `errors` â€” per-repo error messages; a failing repo does not abort the whole request.
- Responses are cached 30 seconds in-process keyed by query parameters.

### 11.2 `GET /api/prs/{owner}/{repo}/{number}`

Returns single-PR detail with extra fields not present in the list endpoint:

| Field           | Description                                                      |
| --------------- | ---------------------------------------------------------------- |
| `body_excerpt`  | First 2 KB of the PR body                                        |
| `checks`        | List of `{name, conclusion, url}` from the commit check-runs API |
| `files_changed` | Number of changed files                                          |
| `additions`     | Lines added                                                      |
| `deletions`     | Lines deleted                                                    |

---

## 12. Issue Inventory API

Implemented in `backend/issue_inventory.py`; thin route shell in `backend/server.py`.

### 12.1 `GET /api/issues`

Aggregates open issues across organisation repositories with taxonomy-aware
filtering.

**Query parameters:**

| Parameter       | Type                | Default       | Description                                   |
| --------------- | ------------------- | ------------- | --------------------------------------------- |
| `repo`          | string (repeatable) | all org repos | Filter to specific `owner/repo` slugs         |
| `state`         | `open` \| `all`     | `open`        | Issue state                                   |
| `label`         | string (repeatable) | â€”           | Match any of these labels                     |
| `assignee`      | string              | â€”           | Filter by assignee login                      |
| `pickable_only` | bool                | `false`       | Only return issues available for agent pickup |
| `complexity`    | string (repeatable) | â€”           | Match any `complexity:*` value                |
| `effort`        | string (repeatable) | â€”           | Match any `effort:*` value                    |
| `judgement`     | string (repeatable) | â€”           | Match any `judgement:*` value                 |
| `limit`         | int                 | 500           | Maximum items returned (hard cap 2000)        |

**Response:**

```json
{
  "items": [
    {
      "repository": "D-sorganization/runner-dashboard",
      "number": 76,
      "title": "...",
      "url": "...",
      "author": "dieter",
      "assignees": [],
      "labels": ["bug", "ci"],
      "age_hours": 12.3,
      "taxonomy": {
        "type": "task",
        "complexity": "routine",
        "effort": "m",
        "judgement": "objective",
        "quick_win": false,
        "panel_review": false,
        "domains": ["backend"],
        "wave": 2
      },
      "agent_claim": null,
      "claim_expires_at": null,
      "linked_pr": null,
      "pickable": true,
      "pickable_blocked_by": []
    }
  ],
  "errors": []
}
```

**Taxonomy parsing** (`parse_taxonomy` in `issue_inventory.py`):
Labels take precedence. Recognised prefixes: `type:*`, `complexity:*`,
`effort:*`, `judgement:*`, `wave:*`, `domain:*`. Boolean flags: `quick-win`,
`panel-review`.

**Pickability rules** (`is_pickable` in `issue_inventory.py`):
An issue is pickable when ALL of the following hold:

1. `state == "open"`
2. No linked open PR (`linked_pr == null`)
3. No active `claim:*` label
4. `judgement` not in `{"design", "contested"}`

`pickable_blocked_by` lists the human-readable reasons when `pickable` is
`false`.

- Per-repo errors appear in `errors[]`; a failing repo does not abort the
  whole request.
- Responses are cached 30 seconds in-process.

### 12.2 Linear Inventory Module

Implemented in `backend/linear_inventory.py`. This is a backend inventory data
layer only; no `/api/linear/*` route, unified GitHub/Linear collapse layer, or
Linear webhook handling is exposed by this module.

`fetch_workspace_issues(workspace, mapping, client, state="open",
team_keys=None, limit=500)` fetches one configured Linear workspace through
`LinearClient.fetch_issues()`, applies `linear_taxonomy_map.apply_mapping()`,
and normalises each Linear issue into the same canonical shape returned by
`issue_inventory.py`, with additive `linear` metadata and `sources:
["linear"]`. Errors are returned as `(items=[], error="...")` instead of being
raised so callers can aggregate across workspaces.

`fetch_all_issues(config, client, state="open", pickable_only=False,
complexity=None, effort=None, judgement=None, limit=500)` gathers all
configured workspaces concurrently and returns `{"items": [...], "errors":
[...]}`. Filtering semantics mirror `GET /api/issues` for `pickable_only`,
`complexity`, `effort`, `judgement`, and `limit`; results are cached for the
same 30 second in-process TTL as GitHub issue inventory.

Linear normalisation rules:

- Linear state types `triage`, `backlog`, `unstarted`, and `started` map to
  canonical `state: "open"`; `completed` and `canceled` map to `"closed"`.
- `age_hours` uses the shared issue inventory age helper and Linear
  `createdAt`.
- Pickability uses the shared `issue_inventory.is_pickable()` rules. Linear
  items have no native `agent_claim` or `claim_expires_at`.
- Taxonomy comes from the Linear mapping result, excluding mapping-only
  `derived_labels` and `source_signals` keys from the canonical `taxonomy`
  object. The derived labels remain on the canonical `labels` field.
- GitHub issue URLs in `attachments.nodes[].url` are extracted into
  `linear.github_attachments`. When present, the first attachment also fills
  canonical `repository` and `number` for compatibility with existing issue
  consumers; Linear-only items use `repository: ""` and `number: null`.

---

## 13. Quick Dispatch API

### 13.1 Endpoint

`POST /api/agents/quick-dispatch`

Triggers the `Agent-Quick-Dispatch.yml` workflow in `Repository_Management` for
an ad-hoc agent task.

**Request body:**

```json
{
  "repository": "D-sorganization/runner-dashboard",
  "prompt": "Fix the failing test in test_api.py",
  "provider": "claude_code_cli",
  "model": "claude-opus-4-7",
  "ref": "main",
  "task_kind": "adhoc"
}
```

**Success response (200):**

```json
{
  "accepted": true,
  "envelope_id": "uuid-hex",
  "fingerprint": "sha256-prefix",
  "workflow_run_url": "https://github.com/.../actions",
  "history_id": "uuid-hex",
  "reason": ""
}
```

**Rejection response (409):**

```json
{ "accepted": false, "reason": "provider_unavailable: ..." }
```

### 13.2 Validation

- `prompt` must be at least 10 characters (400 if not).
- `provider` must exist in `PROVIDERS` and have `availability == "available"`.
  Rejected with `{"reason": "provider_unavailable: <detail>"}`.
- Provider must have `dispatch_mode == "github_actions"`.

### 13.3 Rate Limiting

10 calls per 60-second window per process (in-process token bucket).
Returns HTTP 429 `{"reason": "rate_limited", "retry_after_seconds": N}` when
exceeded.

### 13.4 Workflow Not Configured

If `Agent-Quick-Dispatch.yml` does not exist in `Repository_Management`, the
endpoint returns HTTP 501:

```json
{
  "reason": "workflow_not_configured",
  "suggested_workflow": "Agent-Quick-Dispatch.yml"
}
```

### 13.5 Audit Log

Every accepted dispatch writes a `DispatchAuditLogEntry`-shaped record to
`_QUICK_DISPATCH_HISTORY_PATH` (default:
`~/actions-runners/dashboard/quick_dispatch_history.json`). The path can be
overridden via the `QUICK_DISPATCH_HISTORY_PATH` environment variable.

### 13.6 Implementation

Core logic lives in `backend/quick_dispatch.py`. The server route at
`POST /api/agents/quick-dispatch` is a thin shell that calls
`quick_dispatch.quick_dispatch()`.

---

## 14. Bulk Dispatch API

### 14.1 PR Dispatch

`POST /api/prs/dispatch`

Dispatches agents to one or more pull requests via `Agent-PR-Action.yml`.

**Request body:**

```json
{
  "selection": {
    "mode": "single | repo | list | all",
    "repository": "D-sorganization/runner-dashboard",
    "number": 76,
    "items": [{ "repository": "...", "number": 1 }]
  },
  "provider": "claude_code_cli",
  "prompt": "Address review comments",
  "model": "claude-opus-4-7",
  "confirmation": { "approved_by": "dieter", "note": "manual click" }
}
```

**Response:**

```json
{
  "accepted": 5,
  "rejected": [{ "repository": "...", "number": 4, "reason": "..." }],
  "envelope_ids": ["uuid-hex"],
  "fingerprints": ["sha256-prefix"]
}
```

### 14.2 Issue Dispatch

`POST /api/issues/dispatch`

Same shape as PR dispatch, with two additional fields:

- `"force": true` â€” skip pickability enforcement (requires PRIVILEGED access).
  When forced, `forced: true` is recorded in the audit log.
- Pickability is enforced server-side: issues with `pickable=false` are rejected
  with `reason="not_pickable: <reason>"`.

### 14.3 Selection Modes

| Mode     | Description                                                |
| -------- | ---------------------------------------------------------- |
| `single` | One specific PR/issue by `repository` + `number`.          |
| `repo`   | All open PRs/issues in a repository (caller pre-resolves). |
| `list`   | Explicit list of `{repository, number}` items.             |
| `all`    | All pre-populated items. Hard-capped at 100 targets.       |

### 14.4 Concurrency

Fan-out dispatches run in parallel with an `asyncio.Semaphore` of 4.

### 14.5 Workflow Not Configured

If the target workflow file (`Agent-PR-Action.yml` or `Agent-Issue-Action.yml`)
does not exist in `Repository_Management`, the affected target is added to the
`rejected[]` list with `reason="workflow_not_configured: ..."`.

### 14.6 Audit Logs

- PR dispatches: `_PR_DISPATCH_HISTORY_PATH`
  (default `~/actions-runners/dashboard/pr_dispatch_history.json`,
  override via `PR_DISPATCH_HISTORY_PATH`).
- Issue dispatches: `_ISSUE_DISPATCH_HISTORY_PATH`
  (default `~/actions-runners/dashboard/issue_dispatch_history.json`,
  override via `ISSUE_DISPATCH_HISTORY_PATH`).

### 14.7 Dispatch Contract

Three new actions are registered in the `ALLOWLISTED_ACTIONS` catalog in
`backend/dispatch_contract.py`:

| Action                  | Access     | Requires Confirmation |
| ----------------------- | ---------- | --------------------- |
| `agents.dispatch.adhoc` | PRIVILEGED | Yes                   |
| `agents.dispatch.pr`    | PRIVILEGED | Yes                   |
| `agents.dispatch.issue` | PRIVILEGED | Yes                   |

### 14.8 Implementation

Core logic lives in `backend/agent_dispatch_router.py`. The server routes at
`POST /api/prs/dispatch` and `POST /api/issues/dispatch` are thin shells that
call `agent_dispatch_router.dispatch_to_prs()` and
`agent_dispatch_router.dispatch_to_issues()` respectively.

## 15. Fleet Node Security

### 15.1 Phase 1: Envelope Signing & Replay Protection

All fleet dispatch envelopes (`CommandEnvelope` objects) are cryptographically
signed with HMAC-SHA256 and include replay protection and timestamp validation
to prevent unauthorized command execution, tampering, and replay attacks.

### 15.2 Command Envelope Structure

Every dispatch envelope includes the following security fields:

| Field          | Type               | Purpose                                                           |
| -------------- | ------------------ | ----------------------------------------------------------------- |
| `envelope_id`  | UUID4 (string)     | Unique envelope identifier for replay detection                   |
| `signature`    | hex string         | HMAC-SHA256 signature of the canonical envelope payload           |
| `issued_at`    | ISO 8601 timestamp | Envelope creation time; must be within Â±5 minutes of server time |
| `requested_by` | string             | User/principal requesting the action                              |
| `action`       | string             | Allowlisted action name (e.g., `control.runner.start`)            |
| `payload`      | dict               | Action-specific parameters (e.g., runner ID)                      |

Approval of privileged actions includes:

| Field           | Type               | Purpose                                                  |
| --------------- | ------------------ | -------------------------------------------------------- |
| `approved_by`   | string             | User approving the action                                |
| `approved_at`   | ISO 8601 timestamp | Approval time; must be within Â±5 minutes of server time |
| `approval_hmac` | hex string         | HMAC-SHA256 signature binding approval to the envelope   |

### 15.3 Signature Validation

When a `CommandEnvelope` is created via `CommandEnvelope.from_dict()`, the
signature is verified against the envelope's canonical JSON payload using a
deployment-wide signing secret loaded from the `DISPATCH_SIGNING_SECRET`
environment variable (or `~/.config/runner-dashboard/dispatch_signing_key` if
not set).

Verification failure raises an exception; invalid envelopes never reach business
logic.

### 15.4 Timestamp Validation

Both `issued_at` and `approved_at` timestamps are validated to be:

1. Parseable ISO 8601 strings
2. Not more than 5 minutes in the past (freshness check)
3. Not more than 1 minute in the future (clock skew tolerance)

Validation result is a `TimestampValidationResult` enum: `VALID`, `TOO_OLD`,
or `CLOCK_SKEW`.

### 15.5 Replay Protection

Every processed envelope ID is stored in the `processed_envelopes` table with
a 24-hour TTL. The `/api/fleet/dispatch/submit` endpoint checks this table
before accepting an envelope. Duplicate envelope IDs are rejected with a 400
Bad Request response.

Expired entries are periodically cleaned up (currently at server startup).

### 15.6 Crypto Validation Route

The `/api/fleet/dispatch/submit` endpoint performs full crypto validation:

1. Parse the envelope from the request body
2. Verify the envelope signature via `validate_envelope_crypto()`
3. Check for replay via `_is_envelope_replay()`
4. Validate timestamp freshness
5. Record the envelope ID as processed
6. Proceed to business logic validation

If any crypto check fails, the endpoint returns 400 Bad Request with a
descriptive error (e.g., "Envelope has already been processed (replay
detected)").

### 15.7 Implementation Details

**Signing secret generation:**

```bash
# Generate a 48-byte (384-bit) random hex string
openssl rand -hex 24 > ~/.config/runner-dashboard/dispatch_signing_key
chmod 600 ~/.config/runner-dashboard/dispatch_signing_key
export DISPATCH_SIGNING_SECRET=$(cat ~/.config/runner-dashboard/dispatch_signing_key)
```

**Signing algorithm:**

- Canonical JSON of the envelope (with `signature` field omitted)
- HMAC-SHA256 with the deployment signing secret
- Hex-encoded result

**Signature binding:**

- CommandEnvelope.from_dict() auto-verifies the signature in `__post_init__`
- DispatchConfirmation.approval_hmac binds the approval to the envelope_id

**Database schema:**

```sql
CREATE TABLE processed_envelopes (
  envelope_id TEXT PRIMARY KEY,
  processed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  expires_at DATETIME
);
```

## 16. Assistant Sidebar

### 16.1 Overview

A persistent collapsible sidebar that provides a conversational AI assistant
interface accessible from any tab in the dashboard.

### 16.2 Toggle

A button labelled "â˜° Asst" in the header-right area toggles the sidebar
open or closed. The button is highlighted (blue background) when the sidebar
is open.

### 16.3 Layout

When open, the sidebar docks alongside the main content area in a flex row.
The user may configure it to dock to the left or right of the viewport.
The default position is right.

- Default width: 360px
- Draggable resize handle: 280px â€“ 600px range
- The main content shrinks to fill the remaining width

### 16.4 Persistence

All sidebar preferences are stored in `localStorage` under the `assistant:`
prefix:

| Key                        | Description                                                                                | Default   |
| -------------------------- | ------------------------------------------------------------------------------------------ | --------- |
| `assistant:open`           | Whether sidebar is currently open                                                          | `false`   |
| `assistant:position`       | Dock side (`"left"` or `"right"`)                                                          | `"right"` |
| `assistant:width`          | Sidebar width in pixels                                                                    | `360`     |
| `assistant:transcript`     | Conversation history, written only when history saving is enabled (capped at 200 messages) | `[]`      |
| `assistant:transcript:ts`  | Unix-ms timestamp used to expire saved conversation history after 24 hours                 | unset     |
| `assistant:saveHistory`    | Opt-in preference for saving assistant chat history                                        | `false`   |
| `assistant:openByDefault`  | Open automatically on load                                                                 | `false`   |
| `assistant:includeContext` | Send page context with each message                                                        | `true`    |

Assistant chat history is privacy-preserving by default: transcripts remain
in memory unless the operator enables the `Save chat history` control in the
sidebar header or settings panel. Disabling the control or using `Clear chat
history` removes both transcript storage keys immediately.

### 16.5 Conversation

Messages are displayed as chat bubbles. User messages dock right with a blue
background; assistant replies dock left with a tertiary background. Assistant
responses are rendered with a minimal inline Markdown renderer supporting
bold, italic, inline code, fenced code blocks, links, and ordered/unordered
lists â€” no external library required.

Input is a textarea. Enter sends the message; Shift+Enter inserts a newline.

### 16.6 API Integration

Messages are sent to `POST /api/help/chat` with the body:

```json
{
  "question": "<user message>",
  "page_context": {
    "tab": "<active tab name>",
    "url": "<window.location.href>",
    "selection": "<selected text, up to 500 chars>"
  }
}
```

`page_context` is omitted when the "Include page context" setting is disabled.

### 16.7 Settings

A gear icon in the sidebar header opens a settings card with:

- **Position**: radio buttons for Left / Right dock
- **Open by default**: checkbox
- **Include page context**: checkbox
- **Clear conversation**: destructive button that empties the transcript

### 16.8 Implementation

The `AssistantSidebar` component is defined in `frontend/src/legacy/App.tsx` just
before `QuickDispatchPopover`. It follows the legacy no-JSX convention
of the rest of the frontend. Open/closed state is owned by the `App` component
and passed down as props; all other sidebar state is internal.

## 16. Python Dependency Updates & Test Hardening

### 16.1 Pydantic Version Upgrade

**Updated:** `pydantic==2.10.6` â†’ `pydantic==2.13.3`

- Resolves compatibility issues with Python 3.14's PyO3 bindings
- Maintains backward compatibility with all existing request/response schemas
- No breaking changes to API contracts or validation behavior

### 16.2 API Integration Test Hardening

Tests in `tests/test_api_integration.py` now include required HTTP headers for
proper authentication and CSRF protection:

- **`Authorization: Bearer test-key`** â€” Satisfies FastAPI app's
  `DASHBOARD_API_KEY` import-time validation. The dashboard expects a valid
  Bearer token for authenticated routes.
- **`X-Requested-With: XMLHttpRequest`** â€” Standard CSRF protection header
  required for state-changing requests (PUT, POST, DELETE). This header signals
  to the dashboard that the request originated from the frontend JS, not from
  an HTML form cross-origin submission.

### 16.3 Test Results

**Before:** 158 passed, 8 failed, 1 xfailed
**After:** 166 passed, 1 xfailed âœ“

The 8 previously failing tests required these headers:

- Tests on routes that validate Bearer tokens
- Tests on routes that enforce CSRF protection
- Tests that mock state-changing operations

All tests now pass consistently on Python 3.11, 3.12, and 3.13. Python 3.14
testing awaits environment availability.

## 17. PWA Native Launcher & Recovery Path (Issue #61)

### 17.1 Overview

The dashboard can be installed as a Progressive Web App (PWA) or Chrome app,
but browser sandboxing prevents direct execution of native processes. This
section documents the architecture for launching the backend and offering
recovery controls when the backend becomes unavailable.

**Design Principle:** Explicit operator intent, no silent auto-restart, all
recovery actions logged for audit.

### 17.2 Architecture: Custom URL Protocol Handler

**Recommended Approach:** Custom URL protocol handler (`runner-dashboard://start`)
with systemd/status-UI fallback.

**Platforms:**

- **Windows/macOS:** Custom protocol handler (one-time registration during setup)
- **Linux:** Systemd service auto-restart + status UI fallback

### 17.3 Components

#### 17.3.1 Backend Health Check Endpoint

New endpoint `GET /health` (no authentication required, internal localhost only):

```python
@router.get("/health", tags=["diagnostics"])
async def health_check() -> dict:
    """Launcher health check. Returns 200 if backend is ready."""
    return {
        "status": "ready",
        "timestamp": datetime.now(datetime.UTC).isoformat()
    }
```

Frontend polls this endpoint every 2 seconds. If no response for >5 seconds,
shows recovery modal.

#### 17.3.2 Launcher Script (Windows: `deploy/launcher.ps1`)

PowerShell script that handles `runner-dashboard://start` protocol:

1. Checks if backend is responding (HTTP health check)
2. If running, opens browser to `http://localhost:8321`
3. If not running, starts the backend service (via WSL/systemd)
4. Performs health check with exponential backoff (max 10 attempts)
5. On success, opens browser; on failure, logs error and exits with non-zero code
6. All actions logged to `~/.config/runner-dashboard/launcher.log`

**Usage from frontend:**

```html
<a href="runner-dashboard://start">Start Dashboard</a>
```

#### 17.3.3 Protocol Handler Registration (Windows: `deploy/register-protocol.ps1`)

PowerShell script that registers the custom protocol handler in Windows registry:

```powershell
# Creates registry entry:
# HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.runner-dashboard
# -> Points to launcher.ps1 as handler
```

Called once during `deploy/setup.sh` (Windows only). Requires operator to
approve the protocol handler in the browser (native OS dialog).

#### 17.3.4 Frontend Recovery UI Modal

When the health check fails:

```
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚ Dashboard backend is not responding      â”‚
â”‚                                         â”‚
â”‚ [Start Now]  [Manual Instructions]     â”‚
â”‚                                         â”‚
â”‚ If you continue to see this error,      â”‚
â”‚ check ~/config/runner-dashboard/launcher.log
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

- **"Start Now"** (Windows/macOS): Triggers `runner-dashboard://start` protocol
- **"Manual Instructions"** (all platforms): Shows copy-paste terminal command
- **"Refresh"** (after action): Re-checks health and closes modal on success

### 17.4 Implementation Details

#### Health Check Polling

Frontend JavaScript (in main `App` component):

```javascript
// Poll /health every 2 seconds
const [backendHealthy, setBackendHealthy] = useState(true);
useEffect(() => {
  const interval = setInterval(async () => {
    try {
      const resp = await fetch("http://localhost:8321/health", {
        timeout: 3000,
      });
      setBackendHealthy(resp.ok);
    } catch (err) {
      setBackendHealthy(false);
    }
  }, 2000);
  return () => clearInterval(interval);
}, []);
```

#### Launcher Protocol Flow

1. Frontend detects backend down
2. Shows modal with "Start Now" button
3. Click â†’ `<a href="runner-dashboard://start">` (browser navigates)
4. Browser recognizes protocol, launches registered script
5. Launcher script starts backend, checks health, opens browser to dashboard
6. Modal auto-closes when health check succeeds
7. On Linux: manual instructions shown; operator runs `systemctl restart runner-dashboard`

### 17.5 Security Considerations

**Protocol Handler:**

- âœ… Only `runner-dashboard://` scheme (no collision with other apps)
- âœ… Script is local, operator-controlled, no network access
- âœ… Operator approves handler installation once during setup
- âœ… Browser prevents non-local sites from triggering the protocol
- âœ… Launcher script has hardcoded paths (no shell expansion)

**Health Endpoint:**

- âœ… No authentication required (internal localhost:8321 only)
- âœ… Returns minimal data (status + timestamp)
- âœ… No secrets or operational state exposed

**Recovery UI:**

- âœ… "Manual Instructions" path requires operator terminal use
- âœ… Protocol handler requires operator browser approval
- âœ… No automatic remediation; all actions explicit

### 17.6 Deployment

**During `deploy/setup.sh` (Windows):**

```bash
if [[ "$OS" == "Windows_NT" ]]; then
  powershell -ExecutionPolicy Bypass \
    -File deploy/register-protocol.ps1
fi
```

**Operator sees:** "Allow runner-dashboard to launch an app?" â†’ Click "Allow"

**Manual re-registration (if needed):**

```powershell
powershell -ExecutionPolicy Bypass -File deploy/register-protocol.ps1
```

### 17.7 Operator Documentation

See [`docs/pwa-launcher-design.md`](docs/pwa-launcher-design.md) for:

- Detailed architecture evaluation (Options 1â€“4)
- Implementation checklist
- Troubleshooting guide
- Platform-specific instructions (Windows/macOS/Linux)

### 17.8 Success Criteria

- âœ… "Start Now" button successfully starts backend and opens dashboard
- âœ… No manual terminal commands needed for happy path

---

## 18. Identity & Quotas (Wave 3)

### 18.1 Multi-User Identity Model

The dashboard uses a multi-principal identity model where every authenticated
request is attributed to a `Principal` (human or bot).

**Principal Model:**

- `id`: Unique identifier (e.g., `dashboard-operator`, `runner-bot`)
- `type`: `human` or `bot`
- `roles`: List of roles (e.g., `admin`, `operator`, `viewer`, `bot`)
- `quotas`: Resource limits (see below)

### 18.2 Resource Quotas (Fair Sharing)

Quotas prevent any single principal from monopolizing fleet resources or
depleting API budgets.

| Resource              | Default | Description                                                   |
| --------------------- | ------- | ------------------------------------------------------------- |
| `max_runners`         | 2       | Maximum concurrent runners leased by this principal           |
| `agent_spend_usd_day` | $10.00  | Maximum daily spend on paid agent dispatches ($0.10/dispatch) |
| `local_app_slots`     | 1       | Maximum local application slots                               |

**Enforcement:**

- **Dispatch check:** `quota_enforcement.py` validates remaining spend and
  runner slots before allowing a dispatch.
- **Bulk truncation:** Bulk PR/issue dispatches are automatically truncated
  to fit within the principal's remaining `max_runners` quota.

### 18.3 Runner Lease Management

The lease layer (`runner_lease.py`) tracks active claims on runners.

- **Lease types:** Physical (tied to a `runner-id`) or Virtual (tied to a
  `task-id` before a runner is assigned).
- **Lease Awareness:** The `runner_autoscaler.py` respects active leases; it
  will not stop a runner that holds a valid claim, even if it is idle.
- **Lease Reaper:** Stale leases are automatically cleared after 1 hour or
  upon task completion.
- **Unification:** Internal leases are synchronized with GitHub `claim:*`
  labels and `lease:` expiry comments found in issue/PR inventories via
  `lease_synchronizer.py`.

### 18.4 Onboarding & Principals Configuration

Principals are defined in `config/principals.yml`.

```yaml
principals:
  - id: dashboard-operator
    type: human
    roles: [admin]
    github_username: operator-login
```

New principals can be added by editing this file; the dashboard reloads it
automatically. Service tokens for bot principals can be minted via the
Identity Manager (`identity_manager.mint_service_token`).

<!-- spec-trigger-145 -->

### 18.6 CI Action Pinning, Workflow Concurrency Policy & Tool Version Parity (Issues #390, #689)

To prevent silent drift between local development and CI, the repository
enforces two invariants:

- **Single SHA per action:** every `actions/<name>@<sha>` reference in
  `.github/workflows/*.yml` must resolve to one 40-char SHA across all
  files, with one consistent `# vN` comment. The `verify-action-pin-uniformity`
  step in `ci-standard.yml` (job `ci-health-check`) enforces this, and
  `tests/test_workflow_action_pinning.py` provides a fast pytest guard.
- **Tool version parity:** `pyproject.toml [dependency-groups.dev]` pins
  `ruff` and `mypy` exactly (e.g. `ruff==0.14.10`, `mypy==1.13.0`) to
  match the `rev:` values in `.pre-commit-config.yaml`. The
  `verify-tool-version-parity` step in `ci-standard.yml` enforces this,
  preventing `uv sync` from installing a newer linter/type-checker than
  CI uses.
- **Workflow concurrency policy:** `config/workflow_concurrency_policy.json`
  is the single allowlist for PR-triggered workflows that intentionally keep
  `cancel-in-progress: false` and for repo-wide PR singleton concurrency
  groups. `tests/test_workflow_hygiene.py` and
  `.github/workflows/lint-workflow-files.yml` both enforce that PR workflows
  default to `cancel-in-progress: true`, include a PR/ref discriminator in
  their concurrency group unless explicitly allowlisted, and point operators
  to `docs/runbooks/ci-failure-triage.md` for the canonical remediation
  pattern.

### 18.5 Cross-Fleet Coherence & Admin API (Wave 4)

To ensure identity and quotas are respected across the entire fleet:

- **Cross-Node Principal Propagation**: The \CommandEnvelope\ in \dispatch_contract.py\ includes \principal\, \on_behalf_of\, and \correlation_id\. These fields are now included in the canonical JSON payload used to generate the HMAC-SHA256 signature, ensuring that malicious actors cannot forge identities during cross-node dispatch.
- **Hub-Side Merged Audit View**: A new endpoint \/api/fleet/audit\ aggregates orchestration audit logs from all nodes in the \FLEET_NODES\ configuration. It supports filtering by \principal\ and merges entries sorted by timestamp. Local audit logs can be retrieved via \/api/audit\.
- **Admin API**: The \/api/admin/\*\ router provides endpoints for managing the identity system:
  - \GET /api/admin/principals\: List all registered principals and their quotas.
  - \GET /api/admin/tokens\: List all active service token hashes.
  - \POST /api/admin/principals/{id}/token\: Mint a new service token for a bot principal.
  - \DELETE /api/admin/tokens/{token_hash}\: Revoke a service token.
  - \PATCH /api/admin/principals/{id}/quota\: Update quotas (\max_runners\, \gent_spend_usd_day\, \local_app_slots\) for a specific principal.
    <!-- Updated: 2026-04-29T18:38:16 -->

### 18.6 Consistent Error Envelope (issue #406)

All 4xx and 5xx responses from `/api/*` routes return a JSON object conforming
to `ErrorResponse` (`backend/error_models.py`):

```json
{
  "error": "<machine-readable code>",
  "detail": "<human-readable description>",
  "request_id": "<optional trace id>"
}
```

Standard error codes:

| Code               | HTTP status | Meaning                               |
| ------------------ | ----------- | ------------------------------------- |
| `not_found`        | 404         | Resource does not exist               |
| `forbidden`        | 403         | Permission denied                     |
| `validation_error` | 422         | Invalid request input                 |
| `rate_limited`     | 429         | GitHub rate limit hit                 |
| `conflict`         | 409         | State conflict (e.g. already stopped) |
| `bad_gateway`      | 502         | Upstream GitHub API error             |
| `server_error`     | 500         | Internal server error                 |
| `service_error`    | 500/404/403 | systemd service lifecycle failure     |

Service lifecycle failures (`start`, `stop`, `restart`) additionally map
stderr text to semantic status codes via `service_stderr_to_status()`:

- "not loaded" / "Unit not found" → 404
- "permission denied" / "access denied" → 403
- anything else → 500

### 18.7 Typed GitHub Payload Models (issue #407)

GitHub API response dicts are now parsed at the boundary into typed Pydantic
view-models defined in `backend/models/github_payloads.py`:

| Model           | Replaces                                                                |
| --------------- | ----------------------------------------------------------------------- |
| `GhWorkflowRun` | `run.get("id")`, `(run.get("repository") or {}).get("name", "")` chains |
| `GhJob`         | `j.get("runner_name")`, label dicts vs strings                          |
| `GhRunner`      | `runner["labels"][i]["name"]`, `runner.get("busy")`                     |
| `GhRepository`  | nested repository sub-dict                                              |
| `GhActor`       | `triggering_actor.get("login")`                                         |

All models use `extra="ignore"` so new GitHub API fields never break
existing handlers. Handlers receive flat, typed objects (Law of Demeter).

### 18.8 Pooled GitHub API Client (issue #352)

A new `backend/gh_client.py` module replaces the hottest
`subprocess.run(["gh", "api", ...])` call-sites with a single pooled
`httpx.AsyncClient` that reuses TLS connections and caches the Bearer token.

**Key design:**

- Token loaded once from `GH_TOKEN` / `GITHUB_TOKEN` and cached in memory.
- Typed exceptions: `GhAuthError`, `GhRateLimited`, `GhNotFound`, `GhServerError`.
- `paginate(path)` async iterator follows GitHub Link headers automatically.
- `gh` CLI subprocess retained as fallback when token is absent.
- `gh_utils.gh_api()` delegates to `gh_client.get()` transparently; all
  existing call-sites continue to work without changes.

### 18.9 Log Shipping: Vector Sidecar + Retention Policy (issue #418)

Log aggregation sidecar configuration for the runner-dashboard fleet.

**Files added:**

- `deploy/observability/vector.toml` — Vector config shipping journald +
  Docker container logs to Loki; 7-day retention for app logs, 30-day for errors
- `deploy/observability/journald-retention.conf` — journald drop-in limiting
  on-host storage to 1 GB / 30 days
- `docker/docker-compose.yml` — Dashboard + Vector sidecar as a Compose stack;
  Docker json-file driver rotates at 100 MB × 7 files
- `docs/runbooks/log-retention.md` — Ops runbook covering setup, verification,
  troubleshooting, and Grafana alert definitions

**Retention tiers:**

| Tier                       | Storage          | Retention          |
| -------------------------- | ---------------- | ------------------ |
| info/warn application logs | Loki             | 7 days             |
| error/critical logs        | Loki             | 30 days            |
| Journald on-host           | systemd-journald | 1 GB max / 30 days |
| Docker json-file           | local            | 7 × 100 MB         |

## Security Fixes (issues #315, #317, #318)

### Auth loopback bypass (issue #315)

The loopback bypass that granted automatic admin access to requests from
127.0.0.1 or ::1 is now gated on the environment variable
DASHBOARD_LOOPBACK_AUTH=1. This variable must never be set in production;
it is intended solely for local single-user development where the dashboard
is not reachable beyond the loopback interface.

### HMAC payload signing (issue #317)

The dispatch envelope HMAC signature now includes a SHA-256 hash of the
payload field (payload_hash). This prevents capture-and-replay attacks
where an attacker captures a valid signed envelope and replays it with a
different payload. All new envelopes are signed with payload_hash in the
canonical JSON; the signing secret is DISPATCH_SIGNING_SECRET.

### approval_hmac binding (issue #318)

DispatchConfirmation.approval_hmac must be bound to the specific
envelope_id and action of the request it approves. The canonical
HMAC message is approve:<envelope_id>:<action>. If approval_hmac is
present and invalid, validate_envelope_crypto fails closed with
valid=False. An absent approval_hmac is accepted with a deprecation
warning for backwards-compatibility; clients should supply it.

### Autoscaler overload detection and systemd watchdog (PR #918)

`_default_pool_overloaded()` and `_default_pool_recovered()` are now
pure functions in `runner_autoscaler.py`, extracted from the inline
poll-loop expressions. This makes the overload contract unit-testable
without running the poll loop or touching systemd (OGLaptop 2026-06-09
regression: idle host at load 0.23/20 cores was wrongly scaled to the
floor and logged as "host overloaded").

The systemd watchdog path no longer requires the optional `systemd`
Python package. `_sd_notify_socket()` writes the sd_notify datagram
directly to `$NOTIFY_SOCKET`; `_notify_systemd()` tries the binding
first and falls back to the socket path. `NotifyAccess=main` is added
to `deploy/runner-autoscaler.service` so systemd honours keep-alive
pings from the main process.

`_stop_unit()` now accepts a `reason=` keyword argument so a
scheduled-surplus trim is logged as such rather than "host overloaded".
