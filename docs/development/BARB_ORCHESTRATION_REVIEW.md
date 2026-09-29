# Barb fleet orchestration: implementation review and draft issues

**Board review packet — 2026-09-28 (America/Los_Angeles).** Governing epic: [Runner_Dashboard #1354](https://github.com/D-sorganization/Runner_Dashboard/issues/1354). Review baseline: [`df2f9093`](https://github.com/D-sorganization/Runner_Dashboard/tree/df2f9093db7062ad514ea84a0d0b2501e52c696a), verified against remote `main`. These are proposed issue bodies, not filed issues or approved implementation work. The parent epic remains open.

## Assessment

The current implementation is a credible Staff Console, but it is **not yet a dependable autonomous fleet control plane**. Keep its shared HTTP API, role-based staff model, Barb front door, structured proposals, SQLite history, scoped principals, MCP/CLI clients, run cards and verification machinery. Repair the gaps between these components before adding another orchestration layer.

The most consequential gaps are operational: duplicate execution under concurrent retries; audit failure after a worker starts; follow-up retries that only create database rows; older work excluded from follow-up; and completion inferred from prose. The phone approval workflow also has a navigation obstruction. These are more urgent than visual polish.

Two product decisions must be explicit. “Any computer” presently means a machine with access to the fleet/tailnet; [ADR 0007][ingress] deliberately excludes direct cloud-only access. “Autonomous Barb” presently combines scheduled staff activity with supervised conversation dispatch; it does not mean unrestricted execution of chat requests. The requested future direction should be reviewed as a change to those policies, not silently treated as already implemented.

### What already works and should be retained

| Area               | Existing implementation                                                                        | Remaining concern                                                                                       |
| ------------------ | ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Agent access       | Shared stdlib client, MCP stdio server, CLI, per-agent tokens, briefings and claims            | Installation, session uniqueness, version discovery, consistent v1 coverage and supported network modes |
| Barb conversations | Persistent threads, history/resume, routing, structured action cards, Board/panel helpers      | Reliable targeted reads, complete packet provenance, durable execution/recovery                         |
| Dispatch           | Shared dispatch service, peer targeting, signed caller provenance, policy checks, budgets      | Admission atomicity, receipt persistence, retries and distributed capacity                              |
| Reliability        | Watchdog, orphan reconciliation, bounded provider retries, audit, replay ledger                | Correct ordering, cross-process concurrency, follow-up starvation and lost notifications                |
| UX                 | Staff landing page, grouped roster, typed cards, desktop/mobile layouts, accessible primitives | Truthful operational state, phone navigation, mission continuity and approval clarity                   |
| Completion         | PR/CI verification and Code Request acceptance gates                                           | Ordinary work-item completion still uses textual heuristics                                             |

### Scope, evidence and limits

Reviewed backend routes, clients, identity/approval boundaries, staff routing/context, dispatch/leases/retries, stores, follow-up, verification, frontend shell/console/cards, deployment/test documentation, and the Repository_Management sibling contract. Source was read directly: this checkout has neither `docs/agent_context/catalog.json` nor `docs/codemap.md`. Findings below distinguish **reproduced**, **source-confirmed**, and **design recommendation**. Effort is relative (S/M/L), not a delivery promise.

Validated locally with isolated stores and a disposable backend; no real staff run, live fleet dispatch, Board deliberation, deployment or public ingress was initiated. Browser inspection used real current frontend code with fixture roles/identity, at 1440×1000 and 390×844. This proves those layout observations, not production availability or real provider authentication. Windows required a fixture-only launch adjustment (`USERPROFILE` in the temporary environment); the repository's fake provider CLI is Linux-oriented. Service workers were disabled for fixture bearer injection. Physical-phone keyboard/safe-area behavior and actual Codex/Claude/Grok sessions remain acceptance work.

Known work is not re-filed: #1768 / PR #1771 cover cold-start fleet context; PR #1765 addresses event-loop blocking; #1764 / PR #1769 address the OAuth hint. Baseline includes PR #1772 (action parameters, Board packet reads, other-device approvals), so those fixes are credited. Existing epics #1347–#1354, #1633 and #1718 are parents, not evidence that every end-to-end path is complete. Open-issue/PR check was scoped to this repository on the review date.

## Recommended experience and architecture

The primary journey should be: **connect → receive current direction → ask Barb → inspect the plan and authority → execute within that authority → follow progress → verify results → receive a concise report**. A person should not need to choose a provider, host, specialist or dispatch screen for an ordinary request. Advanced controls remain available.

Keep one authoritative conversation/work ledger behind a stable fleet URL. Execution nodes advertise capabilities and accept durable commands; they do not become independent copies of Barb's memory. Every command carries a mission ID, idempotency key, policy version, originating principal, repository/issue, budget and receipt. Barb explains decisions; deterministic services enforce them. Implement this incrementally around the existing dispatch service and stores, not as a wholesale rewrite.

Separate lifecycle facts: accepted, awaiting authority, admitted, running, awaiting input/CI, verified, done, failed and cancelled. A chat acknowledgement is not admission; a queued row is not a running process; an exit-zero reply is not a verified outcome. Preserve those distinctions in API responses, cards, notifications and summaries.

| Existing surface                | Recommended design direction                                                                                                              |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Staff Console / roster          | Barb and mission history first; specialists as optional destinations; effective availability beside each role                             |
| Inbox / approval cards          | One actionable queue with reason, deadline, eligible approver and exact consequences; phone access must work                              |
| Fleet Command / Assign          | Keep priorities and directives discoverable; route task intake through the same mission/dispatch contract                                 |
| Queue / Remediation / Workflows | Retain operational detail; contextual “Ask Barb about this” carries target IDs and returns to the same mission                            |
| Projects / Code Requests        | Preserve project direction, deferred work and acceptance gates; link parent mission to child issues/PRs                                   |
| Fleet / Operations / Insights   | Secondary operational workspace; current/stale/unknown distinguished; exceptions link back to Barb                                        |
| Settings / credentials          | Guided connection and permission checks, scope expiry/rotation, actionable sign-in state; no secrets in chat                              |
| Maxwell                         | Preserve the sibling HTTP boundary; expose a clear available/unavailable execution capability; do not duplicate its control plane in Barb |

These are information-architecture recommendations from the source/navigation survey, not a claim that every operational page received a live interaction test. The detailed browser inspection covered the Staff Console.

### Board decisions and sequencing

| Decision / wave                   | Recommendation                                                                                             | Drafts              |
| --------------------------------- | ---------------------------------------------------------------------------------------------------------- | ------------------- |
| Immediate correctness             | Fix execution admission, replay and retry paths before increasing unattended dispatch                      | BR-01–03            |
| Reliable ownership and completion | Eliminate starvation/collisions, make outcomes and recovery durable                                        | BR-04–06, BR-15–16  |
| Access policy                     | Support enrolled computers first; decide separately whether cloud-only callers justify revisiting ADR 0007 | BR-07, BR-09        |
| Autonomy policy                   | Approve bounded, revocable mandates, with explicit escalation classes and no self-granted approval         | BR-08, BR-14, BR-17 |
| Usability                         | Repair mobile obstruction and false readiness first; simplify the mission view next                        | BR-10–13            |
| Release gate                      | Require repeatable cross-client, cross-node and recovery evidence before calling the system autonomous     | BR-18               |

All drafts below are **pending Board review**. Proposed labels use the existing taxonomy; verify label availability before filing. For cross-repo architecture decisions, use Repository_Management's formal Board proposal process, then file owner-repo implementation children. Preserve the existing issue-lease rules. Do not close an epic merely because this packet merges.

## Draft issues

### BR-01 — Make staff mutation replay atomic and bind keys to request bodies

**P0 · Runner_Dashboard · L · source-confirmed and reproduced.** Labels: `bug`, `tier:strong`, `judgement:design`. Parent #1347/#1352. Depends on: none.

**Problem/evidence:** [`_handle_idempotent_post`][v1] does lookup → action → save; the [ledger][idempotency] locks individual operations, not the entire admission. Two concurrent same-key calls both invoked the isolated action (observed count: 2). A save failure occurs after the effect and returns retryable 503. There is no payload fingerprint. Thread creation is a separate route without this replay contract, despite the [Grok guide][grok] describing keys on mutating POSTs.

**Proposal:** Reserve a principal/operation/key atomically before effects; record a normalized payload hash, operation ID, pending state and final receipt. Reject changed payloads with 409. Reconcile uncertain outcomes instead of inviting blind replay. Apply a documented contract to thread creation, messages, decisions and dispatch; carry the key across peer forwarding.

**Acceptance:** Concurrent same-key requests across two connections/processes produce one effect; different payloads conflict; a response lost after peer admission returns the original run receipt on retry; crashes before/after ledger commit do not double-dispatch; sequential replay preserves original status/body; TTL and unknown-outcome behavior are tested. Include both thread and dispatch clients.

### BR-02 — Commit dispatch admission and audit before starting a worker

**P0 · Runner_Dashboard · M · source-confirmed and reproduced.** Labels: `bug`, `tier:strong`, `judgement:objective`. Parent #1347. Depends on BR-01's operation identity.

**Problem/evidence:** [Dispatch][dispatch] calls `runner.submit()` before `record_audit(..., fail_closed=True)`. [Submit][runner] persists the run and starts a daemon worker before returning. Injecting audit failure into the shared dispatch service yielded `worker-started` followed by `audit-failed`. The caller can see failure while work is already underway.

**Proposal:** Split validation/admission from worker launch. Commit the command, budget reservation and audit intent together, or use a transactional outbox with a durable audit receipt before consumption. Return one operation/run ID for ambiguous responses. Use the same path for proposals, manual calls, schedules and retries.

**Acceptance:** Audit unavailable means no worker/provider invocation; launch failure leaves a recoverable admitted command; restart after admission starts it once; API retries return the same receipt; audit links initiating principal, policy, proposal, node and run. Fault-injection tests assert order, not only presence of an audit row.

### BR-03 — Make Barb's follow-up retries execute through the real retry service

**P0 · Runner_Dashboard · M · source-confirmed.** Labels: `bug`, `tier:strong`, `judgement:objective`. Parent #1349. Depends on BR-01–02.

**Problem/evidence:** [`FollowupEngine._handle_stalled_or_failed_run`][followup] creates a `queued` RunRecord and links it, but never launches a worker. The [scheduler][scheduler] submits scheduled work and regards active rows as blockers; it is not a consumer of these queued records. The separate [retry implementation][retry] does start workers and has failure-class/budget gates. The follow-up-created record also omits thread/work-item/origin-node provenance.

**Proposal:** Use one durable retry command with bounded attempts and failure classification. Explicitly reconcile or cancel a stalled original before retrying; preserve linkage and policy; make the action discoverable in Barb's thread. Remove the competing row-only retry path.

**Acceptance:** A fixture failed run followed by a sweep causes an actual fake-provider invocation and terminal result; exactly one retry executes across concurrent sweeps/restart; quota, hold and non-retryable refusals remain enforced; remote origin and work links survive; the role cannot be permanently blocked by a phantom queued retry.

### BR-04 — Sweep all due work fairly and persist follow-up decisions

**P1 · Runner_Dashboard · M · source-confirmed and reproduced.** Labels: `bug`, `tier:strong`, `judgement:objective`. Parent #1349. Depends on BR-03.

**Problem/evidence:** [Follow-up][followup] calls `list_work_items(limit=100)` and only then skips terminal items. [The store][workitems] sorts newest first. Creating one overdue item followed by 100 new items excluded the overdue item from that query. Debounce/history/counters are process-local dictionaries; decision scanning separately caps pending proposals at 500.

**Proposal:** Query active, due work in stable deadline order with cursor batches, persist next-check/last-action and bounded retry lineage, and make each decision idempotent. Page overdue proposals too. Keep durable records in existing stores, not a second mailbox.

**Acceptance:** With 1,000 mixed terminal/active records, every overdue item is checked within its SLA plus a sweep interval; new arrivals cannot starve older work; restart does not duplicate retries/escalations; multiple sweep workers claim disjoint work; backlog age, oldest due item and sweep duration are observable.

### BR-05 — Give every agent session a unique, resumable identity

**P1 · Runner_Dashboard; Repository_Management contract owner · S/M · reproduced.** Labels: `bug`, `tier:strong`, `judgement:objective`. Parent #1352. Depends on: none.

**Problem/evidence:** [`default_session`][sessions] derives `<agent>-<host>-<YYYYMMDD>`. Two independent Codex sessions on the same computer/day produce the same value. This undermines presence, mailbox attribution and independent release semantics; the fleet communication contract explicitly requires unique sessions.

**Proposal:** Generate a random session suffix once per logical session; persist/export it for intentional resume and spawned CLI invocations. Preserve the agent prefix and explicit `FLEET_SESSION` override. Separate principal, host, session and run IDs in diagnostics.

**Acceptance:** Two simultaneous clients never share session identity by default; one client keeps its ID across calls/reconnect; deliberate resume reuses it; release by one session leaves the other active; prefix validation and Windows/WSL host naming remain covered by client/MCP tests.

### BR-06 — Define one authoritative Barb endpoint and recover cross-node conversation delivery

**P1 · Runner_Dashboard; Repository_Management topology policy · L · design recommendation grounded in source.** Labels: `enhancement`, `tier:strong`, `judgement:design`. Parent #1192/#1352. Depends on BR-01–02.

**Problem/evidence:** Clients point at an arbitrary [base URL][client]; conversation and run data are node-local SQLite. [Peer board aggregation][fleet] and [remote run lookup][remote] exist, but are not conversation replication. [Run-card relay][runlink] returns failure after a failed peer POST without persisting an outgoing delivery job. Switching endpoints can therefore change which conversations an agent sees; an origin outage can lose a terminal card.

**Proposal:** Choose a stable private service URL and explicit authoritative home for threads/work items. Prefer a single durable authority with tested recovery initially; do not introduce active-active SQLite. Persist outgoing run-card updates with sequence/version, deduplicate at origin, and document read-only/degraded behavior during an authority outage.

**Acceptance:** The same thread is readable from two enrolled computers; a worker completes while the origin is unavailable and its result appears after recovery; duplicate/out-of-order deliveries cannot regress state; a node change is explicit; restoring the authority preserves IDs and receipts. Board chooses availability and recovery objectives.

### BR-07 — Resolve the local-versus-cloud connection policy and contradictory guidance

**P1 · Repository_Management decision; Runner_Dashboard docs/ingress · S for docs, L for a new ingress · source-confirmed policy mismatch.** Labels: `enhancement`, `tier:strong`, `judgement:design`. Parent #1352. Depends on Board decision.

**Problem/evidence:** [ADR 0007][ingress] accepts local/tailnet-only access and rejects staff Funnel/relay activation. [Grok's connection guide][grok], “Remote Ingress & Connector Path,” still says cloud access will use authenticated Funnel. A cloud-hosted model is not the same deployment as a locally executed Grok bridge, Codex CLI or Claude Code.

**Proposal:** Publish a supported-client/network matrix: local process, enrolled remote computer, desktop connector, and cloud-only client. Correct contradictory wording immediately. For cloud-only access, compare a narrowly scoped gateway with an outbound bridge in a formal Board decision; preserve the current boundary until that decision is approved.

**Acceptance:** Each supported client has a tested endpoint, transport, identity and network prerequisite; unsupported modes fail with useful guidance. Any approved new ingress must satisfy the ADR's per-agent scopes, edge rate limits, attribution, caller allowlist and kill switch. No broad dashboard exposure is bundled into documentation work.

### BR-08 — Give Barb bounded standing authority instead of repeated approvals or blanket autonomy

**P1 · Repository_Management policy; Runner_Dashboard enforcement · L · design recommendation.** Labels: `enhancement`, `tier:strong`, `judgement:design`. Parent #1349. Depends on BR-01–04, BR-14, BR-17.

**Problem/evidence:** [Actions][actions] require approved proposals; [decision SLAs][sla] allow silent approval only for low-risk registered actions. [Grok's live contract][grok] requires owner-confirmed dispatch. Scheduled staff and maintenance already have some autonomy, but there is no unified, operator-visible mandate for “work this fleet backlog within these limits.”

**Proposal:** Introduce owner-approved mandates naming allowed portfolios/repos, actions/roles, provider/host constraints, issue tier, spend/concurrency, expiry, stop conditions and escalation policy. Barb may select and dispatch only inside that envelope. Show why each dispatch is covered; revoked/expired mandates stop new admission. Keep merges, public exposure and high-risk changes separately governed.

**Acceptance:** Allowed work proceeds without per-task prompts; out-of-scope work produces one actionable approval; mandates cannot be widened by chat text or peer messages; revocation is checked at admission and retry; every action records the mandate version. Tests cover held work, `do-not-automate`, existing leases and unattended-portfolio restrictions.

### BR-09 — Ship an agent connection doctor and a versioned capability contract

**P1 · Runner_Dashboard; Repository_Management bootstrap distribution · M · design recommendation.** Labels: `enhancement`, `tier:strong`, `judgement:design`. Parent #1352. Depends on BR-05–07.

**Problem/evidence:** [Connection instructions][connect] require checkout paths, Python, environment variables, principal configuration/restart and token minting. [MCP][mcp] exposes a fixed tools table/protocol version; [the client][client] mixes legacy staff reads/dispatch with v1 conversation operations. A installed provider executable is not proof of a working authenticated client or permission to dispatch.

**Proposal:** Package/pin the client, generate OS-specific setup from one manifest, and provide `fleet doctor` with checks for DNS/TLS, auth, scopes, API/schema version, roles, briefing freshness and a harmless Barb round trip. Add effective capabilities/limits and upgrade hints; generate CLI/MCP docs from the same registry. Store credentials via environment/credential-store references, never committed snippets.

**Acceptance:** Fresh Windows, macOS and Linux enrolled hosts connect using documented commands without machine-specific source edits; real Codex and Claude hosts initialize/list/call tools, and the supported Grok local bridge reads/sends/waits; revoked credentials and incompatible versions give precise remedies; read-only credentials cannot gain dispatch/approval privileges; interrupted setup is resumable.

### BR-10 — Make Barb's factual context capability-complete and traceable

**P1 · Runner_Dashboard; Repository_Management role declarations · M · source-confirmed gap and design recommendation.** Labels: `enhancement`, `tier:strong`, `judgement:design`. Parent #1348/#1349. Depends on BR-09; coordinate with #1768 rather than duplicating it.

**Problem/evidence:** [Fleet context][context] explicitly says `read_run` and `read_repo` remain unimplemented; four target-free sources are injected with bounded text and last-good fallback. Baseline now includes referenced-issue/PR reads and larger Board packet budgets. Declaring a tool in a role should not imply a capability the conversation executor cannot supply.

**Proposal:** Resolve targeted read capabilities through typed, bounded handlers. Expose source node, captured-at time, version/SHA, scope and truncation in machine-readable context and the operator view. For long Board packets, persist an artifact manifest and section coverage rather than implying a truncated excerpt is complete.

**Acceptance:** Questions about a named run/repo retrieve that target or say why unavailable; stale facts cannot silently authorize a dispatch; a packet exceeding the context budget reports omitted sections and permits retrieval; unsupported role tools fail validation or display unavailable. Board conclusions cite the actual revision examined.

### BR-11 — Wire role readiness and context controls to real operational data

**P1 · Runner_Dashboard · M · source-confirmed and browser-observed.** Labels: `bug`, `tier:cli`, `judgement:objective`. Parent #1718/#1350. Depends on BR-09 capability shape.

**Problem/evidence:** [`toRoleDetail`][uistate] turns provider strings into `signed_in: true`, sets empty active-run/work-item arrays, and derives schedule enabled from `!retired`. [Desktop][desktop] does not pass provider availability to Roster or a schedule-toggle callback to ContextPane; its thread context contains only the thread ID. The real [context component][contextpane] renders a schedule switch whose handler returns when no callback exists. The fixture's explicitly unavailable role rendered “Idle” with a green dot.

**Proposal:** Load effective provider readiness, holds, schedules, linked runs/work items and freshness from the existing query layer. Render unknown/unavailable explicitly. Wire authorized schedule changes with rollback, or display read-only status; never show an enabled inert switch. Distinguish role validity from ability to execute now.

**Acceptance:** An installed-but-signed-out provider, held role, offline node and unavailable source each render distinctly; a switch either persists the change or explains why disabled; real linked items populate both desktop and mobile context; loading does not show invented readiness. Test the mounted production data path, not only seeded components.

### BR-12 — Keep mobile Inbox and Runs above the global navigation

**P1 · Runner_Dashboard · S · reproduced in browser.** Labels: `bug`, `tier:cli`, `judgement:objective`. Parent #1718/#1350. Depends on: none.

**Problem/evidence:** At 390×844, the Staff Inbox tab occupied y=792–836, but hit-testing its location returned the global mobile-navigation SVG. Both Staff views and the main navigation exist in the accessibility tree; only the latter is visible at the bottom. See [phone capture](barb-review-assets/mobile.png), [mobile console CSS][mobilecss] and [MobileShell][mobileshell]. The composer already has a shell offset; the Staff view tabs need equivalent layout ownership.

**Proposal:** Give the shell sole ownership of the fixed bottom navigation and reserve its height/safe area in the console. Put Console/Inbox/Runs in an unobscured local navigation region. Preserve focus and context across views; surface pending approvals prominently.

**Acceptance:** Inbox, Runs, send and approval controls are visible and hit-testable at 320, 390 and 430px widths; Playwright actually clicks them inside MobileShell; no overlapping navigation at 200% zoom or after keyboard open/close. Verify safe areas and screen-reader order on a physical phone before release.

### BR-13 — Make the home view a Barb mission workspace with recoverable conversation history

**P2 · Runner_Dashboard · M · design recommendation grounded in browser/source review.** Labels: `enhancement`, `tier:strong`, `judgement:design`. Parent #1350/#1353/#1718. Depends on BR-11–12, BR-15.

**Problem/evidence:** [Desktop capture](barb-review-assets/desktop.png) shows global navigation, six Staff tabs, inbox, machine board, roster, thread and context competing for attention. “Board” also means both machine activity and the deliberating Board. Mobile starts with a roster and two Barb entries. [Role-thread resolution][rolethreads] selects the most recent matching thread, not a user-selected mission history.

**Proposal:** Center home on Ask Barb, current missions and Waiting on You; collapse machine metrics into an operational summary. Add named mission/thread history, search, new-conversation and cross-device resume. Keep specialist selection and Assign as explicit advanced tools. Use distinct labels for Board deliberation and fleet activity, and one Barb entry.

**Acceptance:** A new user asks Barb without knowing a role/provider; resumes an older mission without overwriting another conversation; finds a failed/blocked task and its next action in two interactions; preserves drafts across navigation/reconnect; keyboard and screen-reader users complete the same journey. Validate with operator sessions, not solely preference-based screenshots.

### BR-14 — Preview effective approval authority and exact dispatch consequences

**P1 · Runner_Dashboard · M · design recommendation grounded in source.** Labels: `enhancement`, `tier:strong`, `judgement:design`. Parent #1350/#1349. Depends on BR-09, BR-11; policy portion supports BR-08.

**Problem/evidence:** [ActionCard][actioncard] presents risk/params and generic decision controls. [Server policy][actions] separately checks approval scope, action scope and owner-only risk. Baseline correctly adds other-tailnet-device approval while withholding it from same-host loopback; that does not guarantee every displayed decision button is usable by the current principal.

**Proposal:** Return per-proposal effective capabilities and denial reasons. Show target repo/issue, specialist, host/provider, expected cost, mandate or required approver, policy version, expiry and validation result in plain language. Revalidate the exact reviewed parameters at execution. Offer a secure deep link to an eligible approval device rather than repeated chat confirmations.

**Acceptance:** A read-only agent cannot approve its own work; an eligible remote operator can; owner-only actions explain the extra requirement; expired/changed proposals require a fresh decision; denial/retry is recoverable; estimates and actual spend remain distinct. Test real identity-resolution paths and mixed proposal types, not only admin fixtures.

### BR-15 — Derive completion from structured acceptance evidence, not result prose

**P1 · Runner_Dashboard · M · source-confirmed.** Labels: `bug`, `tier:strong`, `judgement:design`. Parent #1349. Depends on BR-03 and BR-06 delivery.

**Problem/evidence:** [`update_linked_work_item`][runlink] sends a succeeded run to `waiting_on_ci` if its summary contains `PR #` or `pull/`; otherwise it marks the work item `done`. [Verification][verification] defaults to report mode and can classify an open green PR as verified. That is useful output verification but is not the same as merged work or a mission's acceptance criteria. Code Requests already have a stronger separate completion gate.

**Proposal:** Define typed outcome/artifact fields and explicit acceptance policy per mission (e.g. draft PR delivered versus merged/deployed). Drive ordinary work-item status from recorded evidence and required gates. Display provider finished, PR ready, merged, deployed and accepted separately; preserve uncertainty.

**Acceptance:** Exit zero with no required artifact cannot close the mission; alternate PR wording cannot change state; red/pending CI and unmerged PRs obey the chosen acceptance policy; external validation stays deferred; Barb reports links, tested revision and unmet checks. Reconcile the ordinary work and Code Request lifecycles without weakening either.

### BR-16 — Recover in-flight proposals and replay updated cards after disconnect

**P1 · Runner_Dashboard · L · source-confirmed gaps; fault-injection work proposed.** Labels: `enhancement`, `tier:strong`, `judgement:design`. Parent #1347/#1348. Depends on BR-01–02, BR-06.

**Problem/evidence:** [Startup reconciliation][reconcile] handles runs and interrupted messages, but has no executing-proposal recovery pass. [Action execution][actions] transitions to executing before invoking an external effect and to done afterwards. [Run cards][runlink] update existing messages; [the thread bus][bus] is in-process and drops events when subscriber queues fill. A durable message sequence alone does not establish reliable replay of later edits to that message.

**Proposal:** Persist operation receipts/outbox events; reconcile executing proposals using external evidence and never blind-retry uncertain effects. Give message/card updates monotonic event revisions separate from message order. Reconnect via an event cursor or authoritative snapshot refresh, with explicit stale/offline state.

**Acceptance:** Crash after a peer/GitHub effect but before proposal completion recovers without duplication; reconnect after a run-card edit shows the terminal state; overflow triggers resync; two backend processes do not lose cross-process updates; cancellation/approval races converge to one audited outcome. Surface unresolved operations to Barb and the owner.

### BR-17 — Reserve fleet capacity and budgets atomically before autonomous fan-out

**P1 · Runner_Dashboard; Repository_Management portfolio policy · L · source-grounded design recommendation.** Labels: `enhancement`, `tier:strong`, `judgement:design`. Parent #1192/#1349. Depends on BR-01–02, BR-06; required before BR-08 rollout.

**Problem/evidence:** [Machine selection][dispatch] chooses from an aggregated board snapshot; [budget checks][budget] compare recorded spend plus per-run estimate. Those checks are valuable, but are not a durable fleet-wide reservation of concurrent slots or remaining shared provider allowance. Multiple initiators can observe the same headroom; node-local records are not automatically a global spend ledger.

**Proposal:** Establish policy-owned limits and atomic reservations keyed by admitted operation, with bounded pending queues, priority/fairness, lease expiry and reconciliation. Reserve estimates, settle actual usage and distinguish unknown quota from available quota. Include retries, panels and scheduled/manual work. Keep notional subscription effort separate from billed API spend.

**Acceptance:** Concurrent requests from two nodes cannot exceed the configured slots/budget; node loss releases or reconciles reservations without double-spend; high-priority repair work gets capacity without cancelling other PR CI; unknown quota follows an explicit policy; Barb explains queue position, refusal and next eligible time.

### BR-18 — Gate autonomous releases on cross-client journeys and recovery drills

**P1 · Runner_Dashboard release evidence; sibling owners for their contracts · M/L · design recommendation.** Labels: `enhancement`, `tier:strong`, `judgement:design`. Parent #1347/#1352/#1718. Depends on BR-01–17 as applicable.

**Problem/evidence:** [The hermetic E2E suite][e2e] uses real FastAPI but fake provider executables, roles and identities; that is the right fast regression layer. Existing suites passed this review while separate reproductions exposed concurrency, admission and layout gaps. Unit/component success cannot establish real client, tailnet, approval-device or cross-node continuity.

**Proposal:** Keep hermetic tests and add a release matrix: real supported agent adapters on enrolled hosts, least-privilege identity, clean/cold boot, live-but-bounded Barb request, approved dispatch to another node, input/CI/PR follow-through, cancellation, lost reply, offline origin, restart and database restore. Record artifact and role-bundle SHAs, actual commands/results and unresolved limits. Use a synthetic read-only canary and explicit availability/latency budgets.

**Acceptance:** CI includes BR-01–05/12 regressions plus linked end-to-end journeys; staging records successful Codex/Claude/local-Grok and phone-approval checks; backup restoration preserves threads, decisions, audit and run receipts with measured RPO/RTO; rollback does not double-dispatch. Board approves measurable service objectives and a bounded pilot before expanding unattended scope. “Flawless” is replaced by evidence, failure budgets and recovery guarantees.

## Validation evidence

At baseline, with the existing project Python environment:

```text
python -m pytest tests/clients tests/api/test_staff_v1_api.py tests/api/test_staff_dispatch_service.py tests/unit/test_staff_actions.py tests/unit/test_staff_reconcile.py tests/api/test_staff_followup.py -q --tb=short
PASS: 190 tests; deprecation warnings only.
node node_modules/vitest/vitest.mjs run frontend/src/pages/StaffConsole frontend/src/shell/__tests__ --reporter=dot
PASS: 54 files, 534 tests. Non-fatal Node localstorage and test-fixture toast/error-report warnings.
```

Four isolated experiments against real functions/stores completed with exit 0 (no provider execution):

| Experiment           | Method                                                                                                                                         | Observed result                       |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| Concurrent replay    | `asyncio.gather` two `_handle_idempotent_post` calls, same principal/key/endpoint, coroutine effect yields briefly, temporary IdempotencyStore | 2 effect invocations                  |
| Session collision    | Two `default_session('codex')` calls for the same host/date                                                                                    | Equal session IDs                     |
| Follow-up starvation | Create overdue item then 100 newer items in temporary WorkItemStore; run the sweep's `list_work_items(limit=100)` query                        | Old item absent                       |
| Audit ordering       | Shared dispatch service with a fake runner recording submit and an audit sink raising; no real worker                                          | `worker-started`, then `audit-failed` |

Browser evidence: real frontend plus temporary fixture backend, fixture operator bearer restricted to local API requests, no real chat turn sent. Viewed desktop and mobile captures. Mobile Inbox tab rectangle was `{x:136.66,y:792,width:116.67,height:44}`; `document.elementFromPoint` inside it returned the global navigation SVG. Opening Ask Barb created a fixture thread successfully. These observations do not assert a live fleet/provider test.

Suggested new tests belong with each implementation issue, not in this documentation-only PR. Source changes were intentionally not made. Full repository/production certification, every provider version, real-network failover and physical-device accessibility were not performed. Follow the existing cold-start fixes separately; do not use this review to claim their deployed validation.

## Pinned source index

All implementation links below are pinned to the reviewed commit so later changes cannot silently alter this packet's evidence. Function names in issue bodies are the navigation anchors; related tests sit alongside the paths listed in the validation section.

[ingress]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/docs/adr/0007-agent-client-ingress-local-only.md
[grok]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/docs/agents/grok.md
[connect]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/docs/agents/connect.md
[v1]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/backend/routers/staff_v1.py#L65
[idempotency]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/backend/staff/idempotency.py
[dispatch]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/backend/staff/dispatch_service.py
[runner]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/backend/staff/runner.py#L186
[followup]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/backend/staff/followup.py
[scheduler]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/backend/staff/scheduler.py
[retry]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/backend/staff/retry.py
[workitems]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/backend/staff/work_items.py
[sessions]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/clients/fleet/fleet_validators.py#L139
[client]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/clients/fleet/fleet_client.py
[mcp]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/clients/fleet/fleet_mcp.py
[fleet]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/backend/staff/fleet.py
[remote]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/backend/staff/remote_runs.py
[runlink]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/backend/staff/run_link.py
[actions]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/backend/staff/actions.py
[sla]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/backend/staff/decision_sla.py
[context]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/backend/staff/chat_fleet_context.py
[uistate]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/frontend/src/pages/StaffConsole/useStaffConsole.ts
[desktop]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/frontend/src/pages/StaffConsole/Desktop.tsx
[contextpane]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/frontend/src/pages/StaffConsole/ContextPane.tsx
[mobilecss]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/frontend/src/pages/StaffConsole/mobile.css
[mobileshell]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/frontend/src/shell/MobileShell.tsx
[rolethreads]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/frontend/src/pages/StaffConsole/consoleThreads.ts
[actioncard]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/frontend/src/pages/StaffConsole/cards/ActionCard.tsx
[verification]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/backend/staff/verification.py
[reconcile]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/backend/staff/reconcile.py
[bus]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/backend/staff/thread_bus.py
[budget]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/backend/staff/budget.py
[e2e]: https://github.com/D-sorganization/Runner_Dashboard/blob/df2f9093db7062ad514ea84a0d0b2501e52c696a/tests/e2e/fakes/README.md
