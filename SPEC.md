# SPEC.md â€” D-sorganization Runner Dashboard

**Spec Version:** 2.5.12
**Application Version:** 4.1.0 (see `VERSION`)
**Last Updated:** 2026-04-29
**Status:** Active

---

## 0. Sibling repos & boundaries

`runner-dashboard` is one of three repos that together form the
D-sorganization fleet operating system. The cross-repo contract is
documented canonically in
[`Repository_Management/docs/sibling-repos.md`](https://github.com/D-sorganization/Repository_Management/blob/main/docs/sibling-repos.md).
Quick form:

- **[`Repository_Management`](https://github.com/D-sorganization/Repository_Management)** â€” fleet orchestrator.
  Publishes shared CI workflows, skills, templates, agent coordination.
  *Does not* own dashboard UI, backend, or HTTP API.
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

**Supporting modules (all in `backend/`):**

| Module | Responsibility |
|---|---|
| `agent_remediation.py` | AI agent dispatch plans, Jules/GAAI/Claude invocation |
| `dispatch_contract.py` | Type contracts for workflow dispatch payloads |
| `machine_registry.py` | Multi-node fleet registry (load, merge with live data) |
| `scheduled_workflows.py` | Inventory of scheduled workflow definitions |
| `deployment_drift.py` | Version drift detection between deployed and expected |
| `local_app_monitoring.py` | Health checks for local process/app registry |
| `usage_monitoring.py` | Per-runner CPU/RAM usage time-series collection |
| `workflow_stats.py` | Aggregate workflow success/failure statistics |
| `report_files.py` | Parse dated report files for the Reports tab |
| `runner_autoscaler.py` | Dynamic runner count scaling logic |
| `config_schema.py` | Config validation and atomic JSON writes |
| `pr_inventory.py` | Fetch and normalise open PRs across repos (issue #80) |
| `issue_inventory.py` | Fetch and normalise open issues with taxonomy (issue #81) |
| `linear_inventory.py` | Fetch and normalise Linear issues into the canonical issue inventory shape |
| `health.py` | Health check endpoints (`/api/health`, `/health`) extracted from server.py (issue #159) |
| `metrics.py` | System metrics endpoints (`/api/system`, `/api/fleet/status`) extracted from server.py (issue #159) |

**Bounded domain routers (`backend/routers/`):**

Well-bounded API domains with no cross-domain shared state are extracted into
`APIRouter` modules and registered with `app.include_router()`. This reduces
coupling and makes each domain independently testable.

| Router | Prefix | Responsibility |
|---|---|---|
| `routers/dispatch.py` | `/api/fleet/dispatch` | Fleet agent dispatcher â€” allowlisted hub-to-node commands |
| `routers/credentials.py` | `/api` | Credential probe â€” tool/key presence without exposing values |
| `routers/linear.py` | `/api/linear` | Optional Linear read API for workspaces, teams, and issue inventory |
| `push.py` | `/api/push` | Web Push subscription storage, scoped unsubscribe, and test-send foundation |

The migration from inline `@app.*` endpoints to bounded routers is ongoing.
Remaining endpoint domains in `server.py` are tracked for extraction under issue #4.

Backend tests must resolve `backend/` imports consistently from a clean checkout.
The project pytest configuration declares `backend` on `pythonpath`, and
`tests/conftest.py` also inserts the resolved backend directory before importing
the FastAPI app and router dependencies.

### 2.2 Frontend

**Type:** Single-Page Application (SPA)
**Entry point:** `frontend/src/main.tsx` (built via Vite)
**Build step:** Vite + React + TypeScript (`npm run build` -> `dist/`)
**JavaScript framework:** React (imported as ES modules)
**createElement API:** `h()` alias (JSX migration in progress)
**Styling:** Extracted CSS in `frontend/src/index.css` (was inline)

Application logic is contained in `frontend/src/legacy/App.tsx` (migrated from
the previous single-file `frontend/index.html`). The Vite build outputs to
`dist/` which the FastAPI backend serves. A `package.json` with build tooling
is now present.
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


PushSettings (issue #192) is a mobile-friendly React component for per-topic Web Push subscription management. It is located at `frontend/src/pages/PushSettings.tsx` and uses `GET /api/push/vapid-public-key` to fetch the VAPID key before subscribing to selected push topics via `POST /api/push/subscribe`.
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

---

## 3. Feature List â€” Dashboard Tabs

### 3.1 Fleet Tab
Real-time view of all self-hosted runners. Displays runner name, status (idle,
active, offline), current job, labels, and systemd service state. Provides
start/stop controls per runner and bulk fleet controls.

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
workflow.

### 3.10 Runner Plan Tab
Fleet autoscaler configuration. Displays current runner count, scaling policy,
schedule-based on/off windows, and allows adjusting the target runner count.

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

### 3.16 Feature Requests Tab
Browse and submit feature request issues via templates. Allows dispatching
feature implementation workflows directly from the dashboard.
On mobile, dispatched feature request history renders as compact read-mostly
cards showing repository, status, vote-count metadata when present, provider,
date, and prompt excerpt over the existing `/api/feature-requests` response.

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

---

## 4. API Endpoint Catalogue

All endpoints are served under `http://localhost:8321/api/`.

### System and Health

| Method | Path | Description |
|---|---|---|
| GET | `/api/system` | Host system metrics (CPU, RAM, disk, GPU) |
| GET | `/api/health` | Simple health check â€” returns `{"status": "ok"}` |
| GET | `/api/watchdog` | Watchdog status and last heartbeat |

### Deployment and Drift

| Method | Path | Description |
|---|---|---|
| GET | `/api/deployment` | Current deployment metadata |
| GET | `/api/deployment/expected-version` | Expected version from repo |
| GET | `/api/deployment/drift` | Version drift between deployed and expected |
| GET | `/api/deployment/git-drift` | Git-commit drift: HEAD vs origin/main with is_drifted flag |
| GET | `/api/deployment/state` | Full deployment state object |
| POST | `/api/deployment/update-signal` | Signal the update mechanism |

### Runners

| Method | Path | Description |
|---|---|---|
| GET | `/api/runners` | All org runners with systemd service state |
| GET | `/api/runners/matlab` | MATLAB-capable runner subset |
| POST | `/api/runners/{runner_id}/stop` | Stop a runner's systemd service |
| POST | `/api/runners/{runner_id}/start` | Start a runner's systemd service |

### Workflow Runs

| Method | Path | Description |
|---|---|---|
| GET | `/api/runs` | Recent workflow runs (all repos) |
| GET | `/api/runs/enriched` | Runs with per-job enrichment data |
| GET | `/api/runs/{repo}` | Runs for a specific repository |
| POST | `/api/runs/{repo}/cancel/{run_id}` | Cancel a workflow run |
| POST | `/api/runs/{repo}/rerun/{run_id}` | Re-run a workflow run |

### Queue

| Method | Path | Description |
|---|---|---|
| GET | `/api/queue` | Current job queue (queued + in_progress) |
| POST | `/api/queue/cancel-workflow` | Cancel a queued workflow |
| GET | `/api/queue/diagnose` | Diagnose queue stalls and blockages |

### Push Notifications

| Method | Path | Description |
|---|---|---|
| POST | `/api/push/subscribe` | Store or update the caller's Web Push subscription and topic list |
| DELETE | `/api/push/subscribe/{subscription_id}` | Remove the caller's subscription; admins may remove any subscription |
| POST | `/api/push/test` | Admin-only test send to the caller's matching subscriptions |
| GET | `/api/push/vapid-public-key` | VAPID public key for Web Push subscription setup |

### Fleet

| Method | Path | Description |
|---|---|---|
| GET | `/api/fleet/status` | Aggregate fleet status summary |
| POST | `/api/fleet/control/{action}` | Bulk fleet action (start-all, stop-all, etc.) |
| GET | `/api/fleet/schedule` | Runner schedule configuration |
| POST | `/api/fleet/schedule` | Update runner schedule |
| GET | `/api/fleet/capacity` | Fleet capacity and utilization |
| GET | `/api/fleet/nodes` | All registered fleet nodes |
| GET | `/api/fleet/hardware` | Per-node hardware specifications |
| GET | `/api/fleet/nodes/{node_name}/system` | System metrics for a specific node |
| GET | `/api/fleet/dispatch/actions` | Available fleet dispatch actions |
| POST | `/api/fleet/dispatch/validate` | Validate a dispatch payload |
| POST | `/api/fleet/dispatch/submit` | Submit a fleet dispatch job |
| GET | `/api/fleet/orchestration` | Orchestration run history |
| POST | `/api/fleet/orchestration/dispatch` | Dispatch an orchestration plan |
| POST | `/api/fleet/orchestration/deploy` | Execute a multi-node deployment |

### Workflows

| Method | Path | Description |
|---|---|---|
| GET | `/api/workflows/list` | All dispatchable workflows in the org |
| POST | `/api/workflows/dispatch` | Manually dispatch a workflow |
| GET | `/api/scheduled-workflows` | Cron-scheduled workflow inventory |

### Repositories

| Method | Path | Description |
|---|---|---|
| GET | `/api/repos` | All org repositories with metadata |

### Reports

| Method | Path | Description |
|---|---|---|
| GET | `/api/reports` | List of available dated report files |
| GET | `/api/reports/{date}` | Report content for a specific date |
| GET | `/api/reports/{date}/chart` | Chart data from a dated report |

### Tests

| Method | Path | Description |
|---|---|---|
| GET | `/api/tests/ci-results` | Latest `ci-standard` run per fleet repo (17 repos, cached 120 s) |
| POST | `/api/tests/rerun` | Re-run failed jobs on a given workflow run (`{repo, run_id}`) |
| GET | `/api/heavy-tests/repos` | Repos eligible for heavy integration testing |
| POST | `/api/heavy-tests/dispatch` | Dispatch a heavy test workflow via GitHub Actions |
| POST | `/api/heavy-tests/docker` | Dispatch a Docker-based heavy test run |

### Stats and Usage

| Method | Path | Description |
|---|---|---|
| GET | `/api/stats` | Aggregate workflow statistics |
| GET | `/api/usage` | Runner usage time-series data |

### Agent Remediation

| Method | Path | Description |
|---|---|---|
| GET | `/api/agent-remediation/config` | Current remediation configuration |
| PUT | `/api/agent-remediation/config` | Update remediation configuration |
| GET | `/api/agent-remediation/workflows` | Eligible workflows for remediation |
| POST | `/api/agent-remediation/plan` | Generate a remediation plan |
| POST | `/api/agent-remediation/dispatch` | Dispatch a remediation plan (GAAI/Claude/Codex) |
| POST | `/api/agent-remediation/dispatch-jules` | Dispatch via Jules API |
| GET | `/api/agent-remediation/history` | Remediation dispatch history |

### Quick Dispatch

| Method | Path | Description |
|---|---|---|
| GET | `/api/agents/providers` | Available agent providers and their availability status |
| POST | `/api/agents/quick-dispatch` | Dispatch an ad-hoc agent task to any repository |

### PR and Issue Dispatch

| Method | Path | Description |
|---|---|---|
| GET | `/api/prs` | List open pull requests across the org with claim/link metadata |
| GET | `/api/prs/{owner}/{repo}/{number}` | Single PR detail with checks and file count |
| GET | `/api/issues` | List open issues with taxonomy and pickability |
| POST | `/api/prs/dispatch` | Bulk-dispatch agent tasks to selected PRs |
| POST | `/api/issues/dispatch` | Bulk-dispatch agent tasks to selected issues |

### Credentials

| Method | Path | Description |
|---|---|---|
| GET | `/api/credentials` | Org and repo secrets/variables inventory (names only) |
| POST | `/api/credentials/set-key` | Securely set an API key for a provider |
| POST | `/api/credentials/clear-key` | Remove an API key for a provider |
| POST | `/api/credentials/launch-auth` | Launch a provider's browser auth flow in a subprocess |

### Runner Audit

| Method | Path | Description |
|---|---|---|
| GET | `/api/runner-routing-audit` | Recent workflow runs on GitHub-hosted runners (billing alert) |
| POST | `/api/runner-routing-audit/refresh` | Trigger an immediate audit refresh |

### Maxwell

| Method | Path | Description |
|---|---|---|
| GET | `/api/maxwell/status` | Maxwell daemon status and configuration |
| POST | `/api/maxwell/control` | Control Maxwell daemon (start/stop/configure) |
| POST | `/api/maxwell/chat` | Proxy Maxwell chat messages over HTTP with streamed text output |

### Assessments

| Method | Path | Description |
|---|---|---|
| GET | `/api/assessments/scores` | Per-repo assessment quality scores |
| POST | `/api/assessments/dispatch` | Dispatch an assessment workflow |

### Assistant (Issues #88, #89)

#### Context-Aware Chat (Issue #88)

| Method | Path | Description |
|---|---|---|
| POST | `/api/assistant/chat` | Query assistant about dashboard state with context |

**Request body:**
```json
{
  "prompt": "Why did this workflow fail?",
  "context": {
    "current_tab": "remediation",
    "selected_run_id": 12345,
    "selected_items": [],
    "dashboard_state": {"...": "..."}
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

| Method | Path | Description |
|---|---|---|
| POST | `/api/assistant/propose-action` | Propose an action based on user request (awaiting approval) |
| POST | `/api/assistant/execute-action` | Execute an approved action with full details |

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
  "parameters": {"runner_name": "runner-5", "timeout_seconds": 300},
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


### Feature Requests

| Method | Path | Description |
|---|---|---|
| GET | `/api/feature-requests` | Feature request issues list |
| GET | `/api/feature-requests/templates` | Available feature request templates |
| POST | `/api/feature-requests/templates` | Create a new feature request template |
| POST | `/api/feature-requests/dispatch` | Dispatch a feature implementation workflow |

### Local Apps

| Method | Path | Description |
|---|---|---|
| GET | `/api/local-apps` | Health status of registered local applications |

### Help

| Method | Path | Description |
|---|---|---|
| POST | `/api/help/chat` | In-app help chat (context-aware AI response) |

### Diagnostics

| Method | Path | Description |
|---|---|---|
| GET | `/api/diagnostics/summary` | Consolidated diagnostics: PID, memory, WSL status, git commit, drift |
| POST | `/api/diagnostics/restart-service` | Restart runner-dashboard systemd service (localhost only) |

### Launchers

| Method | Path | Description |
|---|---|---|
| POST | `/api/launchers/generate` | Generate Windows PowerShell launcher scripts on the Desktop |

### Static Assets

| Method | Path | Description |
|---|---|---|
| GET | `/` | Serves `dist/index.html` |
| GET | `/manifest.webmanifest` | PWA manifest |
| GET | `/icon.svg` | App icon |

---

## 5. Configuration

### 5.1 Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GITHUB_TOKEN` | (required) | GitHub PAT or App token with org runner/workflow scopes |
| `GITHUB_ORG` | `D-sorganization` | GitHub organization name |
| `DASHBOARD_PORT` | `8321` | HTTP port the server listens on |
| `DISPLAY_NAME` | `hostname` | Display name shown in the UI header |
| `NUM_RUNNERS` | `12` | Target number of self-hosted runners |
| `MAX_RUNNERS` | `NUM_RUNNERS` | Hard cap on runner count |
| `RUNNER_DASHBOARD_REPO_ROOT` | Parent of backend dir | Repo root for relative path resolution |
| `DASHBOARD_DISK_WARN_PERCENT` | `85` | Disk usage % threshold for warning state |
| `DASHBOARD_DISK_CRITICAL_PERCENT` | `92` | Disk usage % threshold for critical state |
| `DASHBOARD_DISK_MIN_FREE_GB` | `25` | Minimum free disk GB threshold |
| `RUNNER_ALIASES` | `` | Comma-separated runner name aliases |
| `RUNNER_SCHEDULE_CONFIG` | `~/.config/runner-dashboard/runner-schedule.json` | Path to schedule config |
| `RUNNER_SCHEDULER_BIN` | `/usr/local/bin/runner-scheduler` | Runner scheduler binary path |
| `RUNNER_SCHEDULER_SERVICE` | `runner-scheduler.service` | Scheduler systemd service name |
| `RUN_JOB_ENRICHMENT_LIMIT` | `50` | Max runs to enrich with job data |

### 5.2 machine_registry.yml

Located at `backend/machine_registry.yml`. Defines the multi-node fleet:

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
bash deploy/setup.sh --runners 1 --machine-name Brick-Windows
bash deploy/setup.sh --runners 8 --machine-name OG-Laptop
bash deploy/setup.sh --runners 8 --machine-name DeskComputer --runner-aliases desktop
bash deploy/setup.sh --runners 8 --machine-name ControlTower --role hub \
  --fleet-nodes "Brick-Windows:http://100.64.12.5:8321,OG-Laptop:http://100.64.12.7:8321,DeskComputer:http://100.64.12.9:8321"
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

### 2.5.11 - 2026-04-29
- feat: add authenticated session tracking and remote logout endpoints for the
  mobile auth surface, including hashed session listing and bulk revocation.

### 2.5.10 - 2026-04-29
- feat: add VAPID public key endpoint (`/api/push/vapid-public-key`) and `PushSettings` frontend component with per-topic subscription toggles for Web Push notifications (issue #192).
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

### 9.3 HTTP Security Headers
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

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo` | string (repeatable) | all org repos | Filter to specific `owner/repo` slugs |
| `include_drafts` | bool | `true` | Include draft PRs |
| `author` | string | â€” | Filter by author login |
| `label` | string (repeatable) | â€” | Match any of these labels |
| `limit` | int | 500 | Maximum items returned (hard cap 2000) |

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

| Field | Description |
|---|---|
| `body_excerpt` | First 2 KB of the PR body |
| `checks` | List of `{name, conclusion, url}` from the commit check-runs API |
| `files_changed` | Number of changed files |
| `additions` | Lines added |
| `deletions` | Lines deleted |

---

## 12. Issue Inventory API

Implemented in `backend/issue_inventory.py`; thin route shell in `backend/server.py`.

### 12.1 `GET /api/issues`

Aggregates open issues across organisation repositories with taxonomy-aware
filtering.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo` | string (repeatable) | all org repos | Filter to specific `owner/repo` slugs |
| `state` | `open` \| `all` | `open` | Issue state |
| `label` | string (repeatable) | â€” | Match any of these labels |
| `assignee` | string | â€” | Filter by assignee login |
| `pickable_only` | bool | `false` | Only return issues available for agent pickup |
| `complexity` | string (repeatable) | â€” | Match any `complexity:*` value |
| `effort` | string (repeatable) | â€” | Match any `effort:*` value |
| `judgement` | string (repeatable) | â€” | Match any `judgement:*` value |
| `limit` | int | 500 | Maximum items returned (hard cap 2000) |

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
{"reason": "workflow_not_configured", "suggested_workflow": "Agent-Quick-Dispatch.yml"}
```

### 13.5 Audit Log

Every accepted dispatch writes a `DispatchAuditLogEntry`-shaped record to
`_QUICK_DISPATCH_HISTORY_PATH` (default:
`~/actions-runners/dashboard/quick_dispatch_history.json`).  The path can be
overridden via the `QUICK_DISPATCH_HISTORY_PATH` environment variable.

### 13.6 Implementation

Core logic lives in `backend/quick_dispatch.py`.  The server route at
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
    "items": [{"repository": "...", "number": 1}]
  },
  "provider": "claude_code_cli",
  "prompt": "Address review comments",
  "model": "claude-opus-4-7",
  "confirmation": {"approved_by": "dieter", "note": "manual click"}
}
```

**Response:**
```json
{
  "accepted": 5,
  "rejected": [{"repository": "...", "number": 4, "reason": "..."}],
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

| Mode     | Description |
|----------|-------------|
| `single` | One specific PR/issue by `repository` + `number`. |
| `repo`   | All open PRs/issues in a repository (caller pre-resolves). |
| `list`   | Explicit list of `{repository, number}` items. |
| `all`    | All pre-populated items. Hard-capped at 100 targets. |

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

| Action | Access | Requires Confirmation |
|--------|--------|-----------------------|
| `agents.dispatch.adhoc` | PRIVILEGED | Yes |
| `agents.dispatch.pr` | PRIVILEGED | Yes |
| `agents.dispatch.issue` | PRIVILEGED | Yes |

### 14.8 Implementation

Core logic lives in `backend/agent_dispatch_router.py`.  The server routes at
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

| Field | Type | Purpose |
|-------|------|---------|
| `envelope_id` | UUID4 (string) | Unique envelope identifier for replay detection |
| `signature` | hex string | HMAC-SHA256 signature of the canonical envelope payload |
| `issued_at` | ISO 8601 timestamp | Envelope creation time; must be within Â±5 minutes of server time |
| `requested_by` | string | User/principal requesting the action |
| `action` | string | Allowlisted action name (e.g., `control.runner.start`) |
| `payload` | dict | Action-specific parameters (e.g., runner ID) |

Approval of privileged actions includes:

| Field | Type | Purpose |
|-------|------|---------|
| `approved_by` | string | User approving the action |
| `approved_at` | ISO 8601 timestamp | Approval time; must be within Â±5 minutes of server time |
| `approval_hmac` | hex string | HMAC-SHA256 signature binding approval to the envelope |

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

| Key | Description | Default |
|-----|-------------|---------|
| `assistant:open` | Whether sidebar is currently open | `false` |
| `assistant:position` | Dock side (`"left"` or `"right"`) | `"right"` |
| `assistant:width` | Sidebar width in pixels | `360` |
| `assistant:transcript` | Conversation history (capped at 200 messages) | `[]` |
| `assistant:openByDefault` | Open automatically on load | `false` |
| `assistant:includeContext` | Send page context with each message | `true` |

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
      const resp = await fetch('http://localhost:8321/health', {
        timeout: 3000
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

| Resource | Default | Description |
|---|---|---|
| `max_runners` | 2 | Maximum concurrent runners leased by this principal |
| `agent_spend_usd_day` | $10.00 | Maximum daily spend on paid agent dispatches ($0.10/dispatch) |
| `local_app_slots` | 1 | Maximum local application slots |

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

### 18.5 Cross-Fleet Coherence & Admin API (Wave 4)

To ensure identity and quotas are respected across the entire fleet:
- **Cross-Node Principal Propagation**: The \CommandEnvelope\ in \dispatch_contract.py\ includes \principal\, \on_behalf_of\, and \correlation_id\. These fields are now included in the canonical JSON payload used to generate the HMAC-SHA256 signature, ensuring that malicious actors cannot forge identities during cross-node dispatch.
- **Hub-Side Merged Audit View**: A new endpoint \/api/fleet/audit\ aggregates orchestration audit logs from all nodes in the \FLEET_NODES\ configuration. It supports filtering by \principal\ and merges entries sorted by timestamp. Local audit logs can be retrieved via \/api/audit\.
- **Admin API**: The \/api/admin/*\ router provides endpoints for managing the identity system:
  - \GET /api/admin/principals\: List all registered principals and their quotas.
  - \GET /api/admin/tokens\: List all active service token hashes.
  - \POST /api/admin/principals/{id}/token\: Mint a new service token for a bot principal.
  - \DELETE /api/admin/tokens/{token_hash}\: Revoke a service token.
  - \PATCH /api/admin/principals/{id}/quota\: Update quotas (\max_runners\, \gent_spend_usd_day\, \local_app_slots\) for a specific principal.
