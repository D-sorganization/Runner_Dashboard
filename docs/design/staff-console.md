# Staff Console UX Specification: Information Architecture & Interaction Design

**Status:** Proposed (SC-D1, Issue #1301)  
**Parent Epic:** SC-D (#1350) / Staff Console Umbrella (#1354)  
**Version:** 1.0.0

---

## 1. Executive Summary & Vision

The Runner Dashboard currently presents 31 tabs organized across six groups, defaulting to a low-level fleet infrastructure table. Staff members (AI roles) are buried as a secondary subtab.

This specification elevates **talking to the organization's staff as the primary human-agent interface**. Infrastructure monitoring, work tracking, and settings are organized as supporting domains.

---

## 2. Four-Area Information Architecture

Navigation is restructured into four primary top-level areas:

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Top Bar:  Logo · [Ctrl+K] Search or Ask Staff... · Connection Status · User │
├────────────┬───────────────────────────────────────────────────────────────┤
│ Areas      │ Primary Content Area                                          │
│ 1. Staff   │ ├── Roster Sidebar (Roles, Status, Unread, Search)           │
│ 2. Work    │ ├── Active Conversation Thread (Streaming, Cards, Composer)   │
│ 3. Fleet   │ └── Context Pane (Role Details, Holds, Budget, Active Tasks) │
│ 4. Settings│                                                               │
└────────────┴───────────────────────────────────────────────────────────────┘
```

### 1. Staff (`/` — Default Route)

The conversational cockpit:

- **Left Column:** Staff Roster with search, groups, status dots, and pinning.
- **Center Column:** Interactive Conversation Thread with streaming replies, slash commands, and cards.
- **Right Column (Collapsible):** Context Pane with role mandate, active budget, holds, and related tasks.
- _Redirects:_ Legacy `/` and `/staff` route directly here.

### 2. Work (`/work`)

The operations ledger (SC-C3, SC-F4):

- **Sub-views:**
  - `Inbox / Approvals` (`/work/inbox`): Pending proposals needing human approval.
  - `Work Items` (`/work/items`): Requests tracked to terminal states (`open` -> `done`).
  - `Runs` (`/work/runs`): Execution logs, streaming output, cancellations.
  - `Code Requests` (`/work/code-requests`): Feature suggestions and backlog items.

### 3. Fleet (`/fleet`)

Consolidated infrastructure surface (SC-G2, SC-G3):

- Single unified operations portal merging Machines, Runner Audit, Event Log, Deployment, Orchestration, Diagnostics, and Conductor.
- Machine and runner row actions dispatch maintenance via Barb (`maintenance.*`).

### 4. Settings (`/settings`)

Single settings workspace with clean categorized sections:

- Credentials & Tokens, Linear Setup, Notification Webhooks, Principals & Scopes, Theme & Display, Usage & Pricing.

---

## 3. Core Design Principles

1. **One Obvious Primary Action:** Every view presents a single clear primary CTA (e.g., Send Message, Approve Proposal, Retry Run).
2. **Status Honesty:** Unknown or disconnected states are NEVER displayed as green or healthy. Skeletons and badges clearly distinguish stale data from verified telemetry.
3. **Progressive Disclosure:** Compact summaries by default (status card, run badge); full stack traces, raw JSON, and sub-actions expand on demand.
4. **One Way to Request Work:** All tasks originate as requests to a role ("Ask a role", direct or via Barb), creating audited `ActionProposal`s and `WorkItem`s.
5. **Keyboard-First:** Full keyboard navigation (`j`/`k` thread scrolling, `Ctrl/Cmd+K` palette, `/` slash commands, `Enter` to send, `Escape` to dismiss).
6. **Mobile Parity:** Full mobile responsive design for Staff conversations and approvals via a bottom navigation bar.

---

## 4. Roster Organization & Roles

The roster displays roles grouped by operational tier:

| Tier                 | Primary Role              | Description & Mandate                                                 |
| :------------------- | :------------------------ | :-------------------------------------------------------------------- |
| **Leadership**       | **Ask Barb (auto-route)** | Default top entry. Secretary & attention gate; routes to specialists. |
|                      | Board                     | Collective priority council (`board-secretary` coordination).         |
|                      | Orchestrator              | Fleet strategy and high-level architectural trade-offs.               |
| **Project Managers** | Project Steward           | Cross-repo planning, backlog tracking, roadmap governance.            |
| **Specialists**      | Librarian                 | Documentation integrity, ADR cataloging, cross-references.            |
|                      | Cartographer              | System architecture, dependency graphs, dataflow mapping.             |
|                      | Fleet Critic              | Code quality review, architectural consistency, refactoring.          |
|                      | Research Scout            | Upstream dependencies, paper scans, library evaluations.              |
|                      | OSS Scout                 | Open-source ecosystem alignment, licensing, public PRs.               |
|                      | Pragmatic Programmer      | Targeted bug fixes, test implementation, chore execution.             |
| **Operations**       | Fleet Maintenance         | Runner restarts, cache trim, disk compaction, queue health.           |
|                      | Night Watch               | Automated overnight soak tests, drift alerts, synthetic checks.       |
|                      | Issue Remediator          | Unassisted bug triage, minimal reproducing test generation.           |
|                      | PR Remediator             | Merge conflict resolution, CI failure fixes, rebasing.                |
|                      | Sanitation                | Temporary file cleanup, dead branch pruning, disk hygiene.            |
|                      | Usage Tracker             | Token/LLM spend monitoring, budget caps, credit exports.              |

---

## 5. Responsive Wireframes

### Desktop (Three-Pane Layout)

The desktop layout displays the Staff Roster sidebar, the Direct Thread (conversation pane), and the Context & Action drawer:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ [Logo] Runner Dashboard    [Ctrl+K] Search roles, actions...   (● Online) 👤│
├─────────────────┬───────────────────────────────────────────┬───────────────┤
│ STAFF ROSTER    │ DIRECT THREAD: Barb (Secretary)           │ CONTEXT PANE  │
│ [🔍 Filter roles]│ ───────────────────────────────────────── │ Role: Barb    │
│ 📌 PINNED       │ [User · 10:14 AM]                         │ Status: Idle  │
│ ★ Ask Barb (auto)│ What is the status of the runner fleet?   │ Budget: $2/day│
│ ★ Maintenance   │                                           │ Spend: $0.42  │
│ ─────────────── │ [Barb · 10:14 AM]                         │ ───────────── │
│ ▼ LEADERSHIP    │ All 44 runners are online. Host DeskComp  │ ACTIVE HOLDS  │
│ ● Board         │ listener was wedged but auto-restarted.   │ None active   │
│ ▼ OPERATIONS    │ ┌───────────────────────────────────────┐ │ ───────────── │
│ ● Maintenance   │ │ RUN CARD: maintenance.runner_restart  │ │ RECENT WORK   │
│ ○ Night Watch   │ │ Target: DeskComp-1 · Result: Healthy  │ │ #1322 (done)  │
│ ▼ SPECIALISTS   │ └───────────────────────────────────────┘ │               │
│ ○ Librarian     ├───────────────────────────────────────────┤ [Collapse >>] │
│                 │ [💬 Message Barb... (/dispatch, /status) ] [Send ⮐]       │
└─────────────────┴───────────────────────────────────────────┴───────────────┘
```

### Tablet (Two-Pane with Drawer)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ [☰ Roster]  Barb (Personal Secretary)            (ℹ Context)  (● Online) 👤│
├─────────────────────────────────────────────────────────────────────────────┤
│ [User · 10:14 AM] What is the status of the runner fleet?                   │
│                                                                             │
│ [Barb · 10:14 AM] All runners healthy. DeskComp listener restarted.         │
│ ┌─────────────────────────────────────────────────────────────────────────┐ │
│ │ RUN CARD: maintenance.runner_restart · DeskComp-1 (Succeeded)           │ │
│ └─────────────────────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────────────────┤
│ [💬 Message Barb...                                    ] [Voice] [Send ⮐]   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Mobile (Single-Pane with Bottom Navigation Bar)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ [☰] Barb (Personal Secretary)                                 (ℹ) (● Online)│
├─────────────────────────────────────────────────────────────────────────────┤
│ [User] Check runner pool status.                                            │
│                                                                             │
│ [Barb] 44 runners online. Self-healed 1 wedged listener.                    │
│ ┌─────────────────────────────────────────────────────────────────────────┐ │
│ │ RUN CARD: runner_restart · Status: Done                                 │ │
│ └─────────────────────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────────────────┤
│ [💬 Ask Barb...                                        ] [🎤] [ ⮐ ]         │
├─────────────────────────────────────────────────────────────────────────────┤
│   💬 Staff   │   📋 Work (2)   │   🖥 Fleet (●)   │   ⚙ Settings           │
└─────────────────────────────────────────────────────────────────────────────┘
```

### First-Run & Empty State Wireframe

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ [Logo] Runner Dashboard    [Ctrl+K] Search roles, actions...   (● Online) 👤│
├─────────────────┬───────────────────────────────────────────────────────────┤
│ STAFF ROSTER    │ Welcome to Staff Console (First Run / Empty State)        │
│ [🔍 Filter roles]│                                                           │
│ 📌 PINNED       │   👋 No active conversations yet.                         │
│ ★ Ask Barb (auto)│                                                           │
│ ★ Maintenance   │   Get started by chatting with Barb or delegating a task: │
│ ─────────────── │                                                           │
│ ▼ LEADERSHIP    │   [ 💬 Ask Barb for Fleet Status ]                        │
│ ● Board         │   [ 📋 View Pending Work Approvals ]                      │
│ ▼ OPERATIONS    │   [ 🚀 Dispatch a Maintenance Run ]                       │
│ ● Maintenance   │                                                           │
│                 │                                                           │
├─────────────────┴───────────────────────────────────────────────────────────┤
│ [💬 Ask Barb anything or type /dispatch...                               ]   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Structured Card Specifications

Cards render inline inside conversation streams to represent executable staff capabilities:

### 1. Action Approval Card (`ActionProposal`)

```
┌─────────────────────────────────────────────────────────────────────────┐
│ ⚠ ACTION PROPOSAL · High Risk Gate                                      │
│ Operation: maintenance.runner_stop                                      │
│ Target:    OGLaptop-runner-4 (host: OGLaptop)                           │
│ Rationale: Memory leak detected (> 95% RSS).                            │
│ Proposed By: Fleet Maintenance (10:22 AM)                               │
├─────────────────────────────────────────────────────────────────────────┤
│ [ Approve & Execute ]        [ Deny Proposal ]       [ Edit Parameters ]│
└─────────────────────────────────────────────────────────────────────────┘
```

- **Rules:** Double-click protection via `idempotency_key`; shows who approved/denied and timestamp upon resolution; disables actions when expired (24h TTL).

### 2. Run Progress Card (`RunRecord`)

```
┌─────────────────────────────────────────────────────────────────────────┐
│ ⚙ RUN CARD · maintenance.trim_worktrees                        [Running]│
│ Node: ControlTower · Provider: codex · Elapsed: 14s                     │
│ ─────────────────────────────────────────────────────────────────────── │
│ Log: [10:24:01] Scanning worktree directory /data/worktrees...          │
│      [10:24:08] Found 3 orphaned worktrees (> 7d stale). Deleting...    │
├─────────────────────────────────────────────────────────────────────────┤
│ [ View Full Log ]           [ Cancel Run ]           [ Open PR / Task ] │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3. Error Card (`failure_class` Remediation)

```
┌─────────────────────────────────────────────────────────────────────────┐
│ ✖ ERROR CARD: Authentication Expired                           [Retryable]│
│ Cause: Provider token expired or CLI signed out.                        │
│ Remediation: Run `claude login` on host DeskComputer.                   │
├─────────────────────────────────────────────────────────────────────────┤
│ [ ↻ Retry Turn ]                                 [ Copy Diagnostics ]   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 7. Interaction & Keyboard Contract

- `Ctrl+K` or `Cmd+K`: Open global Command Palette (`CommandPalette.tsx`) across all areas, roles, and actions.
- `/` at start of input: Activate slash commands:
  - `/dispatch <role> <prompt>`: Create formal staff work run.
  - `/review <pr>`: Hand off pull request review to Fleet Critic.
  - `/status`: Request instantaneous fleet & budget status brief.
  - `/hold <rule>`: Place temporary hold on automated actions.
  - `/brief`: Trigger morning/evening one-pager from Barb.
- `@<role>`: Route turn or invite secondary specialist into thread.
- `Enter`: Submit turn (with `Idempotency-Key`).
- `Shift+Enter`: Multi-line code / markdown newline.
- `Escape`: Close open drawers, modals, or cancel active command menu.

---

## 8. Copy Guidelines & Voice

1. **Tone:** Crisp, executive, helpful, and dependable.
   - Barb retains a classic 1950s executive secretary persona (attentive, efficient, light polite sass if Dieter or the operator is the bottleneck).
   - Specialists speak precisely within their domain without conversational fluff.
2. **Plain Language:**
   - Avoid internal engineering jargon in top-level status strings (use "Paused" not "Lease blocked", "Signing in needed" not "401 OAuth token refresh failed").
3. **Standard Action Verbs:**
   - `Approve`: Validate and authorize an action proposal.
   - `Deny`: Reject an action proposal with optional rationale.
   - `Execute`: Run an approved action immediately.
   - `Cancel`: Terminate an active job or run.
   - `Retry`: Re-attempt a retryable failed operation.
   - `Hold`: Impose operational restriction.

---

## 9. State Catalogue & Failure Modes

| State                      | Visual Treatment & Behavior                                                               |
| :------------------------- | :---------------------------------------------------------------------------------------- |
| **Loading**                | Subtle shimmering skeleton loaders preserving layout geometry; no layout shift.           |
| **Empty State**            | Helpful callout with suggested starter actions (e.g. "Ask Barb for fleet brief").         |
| **Partial Failure**        | Visible warning banner identifying failed component while functional parts remain active. |
| **Offline / Reconnecting** | Sticky connection banner with "Reconnecting..."; resumes via `Last-Event-ID`.             |
| **Provider Down**          | Marked with warning badge in roster and tooltip naming missing CLI or API error.          |
| **Node Offline**           | Red dot in machine registry table with timestamp of last heartbeat; proxy fails visibly.  |
| **Permission Denied**      | Standard HTTP 403 banner displaying missing scope (e.g. `staff.approve required`).        |

---

## 10. Traceability & Dependencies

- **Implements:** SC-D1 (#1301)
- **Unblocks:** SC-D2 (#1309 Shell Restructure), SC-D3 (#1317 Roster), SC-D4 (#1318 Thread), SC-D5 (#1319 Cards), SC-D6 (#1320 Context Pane)
- **References:** `docs/staff-hub.md`, `SPEC.md`, SC-B2 (`ConversationStore`), SC-B5 (`ReplyContract`), SC-B6 (`ActionRegistry`), SC-F3 (`/api/v1/staff`)
