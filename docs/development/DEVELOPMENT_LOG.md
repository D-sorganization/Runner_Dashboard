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

### DL-#1313 · SC-B6: Action proposals from conversations with risk-based approval gates

- **State:** in_progress
- **Owner:** antigravity
- **Issue:** #1313 (epic #1348 / umbrella #1354)
- **Branch:** `feat/1313-action-proposals`
- **PR:** #1396
- **Paths:** `backend/staff/actions.py`, `backend/staff/action_executors.py`, `backend/staff/conversation_models.py`, `backend/staff/conversations.py`, `backend/routers/staff_proposals.py`, `backend/routers/assistant.py`, `tests/unit/test_staff_actions.py`, `tests/api/test_staff_proposals_api.py`, `SPEC.md`, `docs/development/DEVELOPMENT_LOG.md`, `docs/development/HANDOFF.md`
- **Started:** 2026-09-24
- **Last verified:** 2026-09-24 (`pytest tests/unit/test_staff_actions.py tests/api/test_staff_proposals_api.py` 15 passed; `pytest tests/clients` 121 passed; ruff clean; mypy 0 errors in 207 files; all modules <= 500 lines)
- **Summary:** Replaced legacy stubs with unified `ActionRegistry` (`staff/actions.py`, `staff/action_executors.py`); approval policies (`read`/`low` auto-execute, `medium` operator approve with `staff.approve` scope, `high`/`owner-only` owner approve); 24h proposal expiry and terminal replay protection; role permission gating (unauthorized roles rejected with 403 Forbidden); post-execution verifiers validating actual state changes; dispatched runs and action outcomes post `action_result` and `run_card` messages back to conversation threads (SC-B7), fully audited in `staff_audit` (SC-A8). Mounted REST endpoints in `backend/routers/staff_proposals.py` under `/api/v1/staff`: `GET /api/v1/staff/actions`, `GET /api/v1/staff/actions/{name}`, `POST /api/v1/staff/proposals`, `POST /api/v1/staff/proposals/{id}/decide` (with immediate execution option), and `POST /api/v1/staff/proposals/{id}/execute`.
- **Next step:** Verify CI passes on PR #1396, auto-merge, and release lease on #1313.

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

## Archive

Older entries live in `DEVELOPMENT_LOG_ARCHIVE_<year>.md`.
