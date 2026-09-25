# Project Charter

> Drafted 2026-09-25 by the fleet charter sweep (Gemini) from README, git history, and open issues/PRs.
> The project-steward role keeps this current; owners should correct feature statuses.

## End Goal

Provide a unified, secure web control console and AI Staff Console for the D-sorganization GitHub Actions self-hosted runner fleet. The dashboard serves as the central operator surface to monitor multi-node hardware and runner health in real time, automate fleet maintenance, purge stale queued runs, manage workflow execution, and interact seamlessly with autonomous AI staff roles through conversational threads, structured approval cards, and shared fleet coordination protocols. "Done" looks like a resilient, authenticated single-pane console where all runner operations, maintenance playbooks, and agent-assisted workflows execute reliably without unvetted manual interventions or fragmented legacy interfaces.

## Non-Goals

- Running Maxwell pipeline execution, Strategist, Crucible, or sandboxes directly (owned exclusively by `Maxwell-Daemon`).
- Fleet-wide CI workflow orchestration, skill definitions, and central governance policies (owned by `Repository_Management`).
- Operating as an unauthenticated or public network service (must fail closed with strict identity, OAuth, and RBAC).
- Executing unverified mutating maintenance actions without dry-run previews and explicit human or policy approval gates.
- Managing standalone local desktop applications outside runner infrastructure and operator diagnostics.

## Features

| ID  | Feature                                 | Status      | Tracking | Notes                                                                     |
| --- | --------------------------------------- | ----------- | -------- | ------------------------------------------------------------------------- |
| F1  | Fleet Monitoring and Machine Inventory  | shipped     | #1324    | Real-time runner statuses, multi-node hardware metrics, and audit log.    |
| F2  | Operations Console and Unified Dispatch | shipped     | #1325    | Merged view for deployments, runner hours, conductor, and schedules.      |
| F3  | Queue Health and Automated Job Reaper   | shipped     | -        | Stale queued run detection, bulk cancellation API, and cleanup daemon.    |
| F4  | Authentication and RBAC Perimeter       | shipped     | #1295    | Session management, OAuth, WebAuthn, and explicit role scopes.            |
| F5  | Staff Console Conversation Engine       | in-progress | #1348    | Threaded chat turns, session resume, and structured action cards.         |
| F6  | Barb Routing and Follow-Up Engine       | in-progress | #1349    | Autonomous task routing, work-item ledger, and stalled-job follow-up.     |
| F7  | Fleet Maintenance Automation            | in-progress | #1351    | Typed maintenance action catalogue, preflight checks, and dry-runs.       |
| F8  | Staff Agent and Tooling APIs            | in-progress | #1352    | Versioned /api/v1/staff API, MCP tools, and external agent connectors.    |
| F9  | Staff Console Shell and Navigation      | in-progress | #1350    | Four-area layout, staff roster sidebar, and mobile responsive shell.      |
| F10 | UI Modernization and Legacy Pruning     | in-progress | #1353    | Retiring classic layout and consolidating off-theme legacy dispatch tabs. |
| F11 | Code Requests and Proposals Pipeline    | in-progress | #1279    | Agent-agnostic planner-to-executor pipeline and board suggestion box.     |
| F12 | Multi-Host Node Infrastructure          | in-progress | #1258    | Node cluster synchronization, self-updating roles, and Ollama bridges.    |
| F13 | Projects and Fleet Stewardship          | in-progress | #1248    | Per-repo charters, status reporting, and deferred-plan owner tracking.    |
| F14 | Staff Hub Reliability and Watchdogs     | in-progress | #1347    | Process tree cleanup, orphan run reconciliation, and error boundaries.    |
| F15 | Maxwell Control Plane Proxy             | shipped     | #1338    | HTTP proxy interface and contract tests for external Maxwell daemon.      |
| F16 | Fleet Coordination and Priorities       | shipped     | #1233    | Priorities API, board meetings, directives, and lease tracking.           |
| F17 | Board Deliberation and Group Threads    | planned     | #1339    | Multi-seat group threads coordinated by Board-Secretary.                  |
| F18 | Offline Release Artifact Pipeline       | parked      | #1085    | Checksummed bundle distribution bypassing machine-local npm installs.     |

## Links

- Status (generated): [`STATUS.md`](STATUS.md)
- Steward playbook: Repository_Management `docs/fleet-project-steward.md`
