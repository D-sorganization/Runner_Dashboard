# SPEC.md — D-sorganization Runner Dashboard

**Spec Version:** 2.0.0
**Application Version:** 4.0.1 (see `VERSION`)
**Last Updated:** 2026-04-24
**Status:** Active

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
5. Serves the frontend SPA (`frontend/index.html`) as a static file at `GET /`.

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

**Bounded domain routers (`backend/routers/`):**

Well-bounded API domains with no cross-domain shared state are extracted into
`APIRouter` modules and registered with `app.include_router()`. This reduces
coupling and makes each domain independently testable.

| Router | Prefix | Responsibility |
|---|---|---|
| `routers/dispatch.py` | `/api/fleet/dispatch` | Fleet agent dispatcher — allowlisted hub-to-node commands |
| `routers/credentials.py` | `/api` | Credential probe — tool/key presence without exposing values |

The migration from inline `@app.*` endpoints to bounded routers is ongoing.
Remaining endpoint domains in `server.py` are tracked for extraction under issue #4.

### 2.2 Frontend

**Type:** Self-contained Single-Page Application (SPA)
**Entry point:** `frontend/index.html`
**Build step:** None — served directly as a static HTML file
**JavaScript framework:** React (loaded from CDN)
**createElement API:** `h()` alias (no JSX, no transpiler)
**Styling:** Inline styles + embedded `<style>` block

All application logic is contained within `frontend/index.html`. There is no
npm project, no `package.json`, and no build toolchain. The file is served
directly by the FastAPI backend.

**Shared helper components** defined near the top of the script block:

- `Collapse` — collapsible section with header and chevron.
- `SubTabs` — horizontal sub-tab strip rendered inside a tab panel. Props:
  `tabs` (array of `{ key, label, badge, disabled }`), `activeKey`, `onChange`,
  `storageKey` (optional localStorage persistence key), `rightBadge` (optional
  element flush-right of the strip). Active tab is persisted to localStorage
  when `storageKey` is provided.

#### Header Quick Dispatch

The main header contains a **Quick Dispatch** button (⚡ Quick Dispatch ▾),
flush-right next to the refresh control. Clicking it opens a popover form that
lets any operator dispatch an ad-hoc agent task to any org repository without
navigating to a specific tab. The popover provides:

- **Repository** dropdown — populated from `GET /api/repos`
- **Provider** dropdown — populated from `GET /api/agents/providers`
- **Model** text field — shown only for providers that support model selection
  (`claude_code_cli`, `codex_cli`); defaults to `claude-opus-4-7`
- **Branch ref** text field — defaults to `main`
- **Prompt** textarea — minimum 10 characters
- **Dispatch** button — POSTs to `POST /api/agents/quick-dispatch`; shows a
  loading state, surfaces errors inline, and auto-closes on success

Click-outside closes the popover. Rate-limit errors (HTTP 429) are surfaced
with a human-readable message.

`frontend/index.html` is the **sole canonical frontend source**. No other
frontend implementation exists in the repository. The previously present
`RunnerDashboard.jsx` was an unused JSX archive that violated DRY; it was
removed in issue #3 to enforce a single source of truth. A CI test
(`test_jsx_archive_removed`) prevents re-introduction of a parallel
implementation.

### 2.3 Deployment

The dashboard runs as a systemd service (`runner-dashboard.service`) on the
primary fleet machine. See Section 6 for deployment details.

---

## 3. Feature List — Dashboard Tabs

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
diagnostic endpoint to explain queue stalls.

### 3.4 Machines Tab
Multi-node fleet hardware inventory sourced from `machine_registry.yml`.
Displays per-node system metrics (CPU, RAM, disk, GPU VRAM) fetched via the
fleet nodes API. Supports drilling into individual node system status.

### 3.5 Organization Tab
Org-level runner and repository summary. Shows runner group assignments,
available label sets, and aggregate health across all repos.

### 3.6 Tests Tab
Unified testing hub with two sections:
1. **CI Tests** — table of the latest `ci-standard` workflow run for each of
   the 17 fleet repos, showing conclusion badge, branch, run number, and
   timestamp. Failed or cancelled runs show a **Re-run Failed** button that
   calls GitHub's `rerun-failed-jobs` API.
2. **Integration Tests** — dispatches and monitors heavy integration test runs
   (MuJoCo, Drake, Pinocchio physics stacks). Lists repos eligible for heavy
   testing, dispatches parameterized workflows, and optionally triggers
   Docker-based test environments.

### 3.7 Stats Tab
Aggregate workflow statistics: success rates, average duration, failure
frequency, and per-repo breakdowns sourced from the `/api/stats` endpoint.

### 3.8 Reports Tab
Displays dated fleet report files (Markdown). Provides date selection and
renders the report with parsed metrics summary cards.

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

- **Automations** (default) — configures and dispatches remediation plans to
  Jules, GAAI, Claude, or Codex agents. Shows dispatch history and plan
  preview. Supports per-repo agent routing and loop-guard configuration.
- **PRs** — multi-select table of open pull requests fetched from
  `GET /api/prs?limit=2000`. Supports filtering by repo, author, and draft
  status. Bulk dispatch sends selected PRs to a chosen provider via
  `POST /api/prs/dispatch` with a confirmation modal.
- **Issues** — taxonomy-aware GitHub Issues browser and bulk dispatcher
  (`GET /api/issues?limit=2000`). Filter bar with repo, complexity,
  judgement, and "pickable only" controls persisted to `localStorage`.
  Multi-select table with type/complexity/effort/judgement pills. Non-pickable
  rows are dimmed; `design`/`contested` judgement pills rendered red with
  warning. Dispatches via `POST /api/issues/dispatch` with optional force flag.

The active sub-tab is persisted to `localStorage` under the key
`remediation-subtab`.

### 3.13 Workflows Tab
Browse and manually dispatch any workflow in any org repository. Supports
input parameter forms generated from workflow `workflow_dispatch` definitions.

### 3.14 Credentials Tab
Inventory of GitHub Actions secrets and variables across the org and per-repo.
Read-only view of credential names (not values) for audit purposes.

### 3.15 Assessments Tab
Dispatch and track code quality assessment workflows (Jules Assessment
Generator). Shows per-repo assessment scores from the `/api/assessments/scores`
endpoint.

### 3.16 Feature Requests Tab
Browse and submit feature request issues via templates. Allows dispatching
feature implementation workflows directly from the dashboard.

### 3.17 Maxwell Tab
Control interface for the Maxwell daemon (fleet orchestration AI). Shows
daemon status, configuration, and provides start/stop/configure controls.

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
| GET | `/api/health` | Simple health check — returns `{"status": "ok"}` |
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

### Maxwell

| Method | Path | Description |
|---|---|---|
| GET | `/api/maxwell/status` | Maxwell daemon status and configuration |
| POST | `/api/maxwell/control` | Control Maxwell daemon (start/stop/configure) |

### Assessments

| Method | Path | Description |
|---|---|---|
| GET | `/api/assessments/scores` | Per-repo assessment quality scores |
| POST | `/api/assessments/dispatch` | Dispatch an assessment workflow |

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
| GET | `/` | Serves `frontend/index.html` |
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
- `pip_install <pkg...>` — Python 3.11-preferring pip with `--break-system-packages` when supported
- `sync_dir <src> <dest>` — rsync with rm/cp fallback
- `backup_dir <path>` — timestamped `cp -a` backup
- `dry_run "<description>"` — no-op gate when `DRY_RUN=true`

All new deploy scripts should source it with:

```bash
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
```

---

## 7. Changelog

### 2.0.0 — 2026-04-23

Initial standalone release. Extracted from the `D-sorganization/Repository_Management`
mono-repo as an independent repository.

- Full FastAPI backend with all API endpoints documented above.
- Self-contained React SPA frontend (no build step).
- Fleet deployment scripts and systemd service unit.
- Fleet-standard CI/CD workflows (ci-standard, ci-spec-check, agent workflows).
- Branch protection with required `quality-gate` and `Verify SPEC.md freshness`
  status checks.
- Multi-agent coordination via lease protocol.

Prior versions tracked in the mono-repo `Repository_Management`. Application
version history in `VERSION` file (4.0.1 at time of extraction).

---

## 8. Testing

The project test suite lives in `tests/`. Run all tests with:

```
pytest tests/ -q
```

Test coverage areas:

- **`tests/test_dispatch_contract.py`** — unit tests for `backend/dispatch_contract.py`:
  envelope round-trips, confirmation gating for privileged actions, allowlist enforcement.
- **`tests/test_remote_execution_contract.py`** — unit tests for `backend/remote_execution_contract.py`:
  private-host and private-URL detection, unknown-target rejection.
- **`tests/test_agent_remediation.py`** — unit tests for `backend/agent_remediation.py`:
  `FailureContext` construction, workflow-type classification for lint and test workflows,
  policy defaults.
- **`tests/test_frontend_integrity.py`** — static source checks for `frontend/index.html`:
  required tab function markers, absence of deprecated `HeavyTestsTab`, icon helper symbols.

`pytest>=8.0` and `pytest-asyncio>=0.23` are listed in `requirements.txt`.

---

## 9. Security

### 9.1 Markdown Rendering
All user-supplied content rendered as Markdown is passed through
`DOMPurify.sanitize()` before `dangerouslySetInnerHTML`. Marked.js is
configured with `{ mangle: false, headerIds: false, gfm: true }`.

### 9.2 HTTP Security Headers
The backend injects the following headers on all responses:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: SAMEORIGIN`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Content-Security-Policy` — allows self, CDN scripts (jsdelivr, cdnjs, unpkg)

### 9.3 Destructive Action Confirmation
Critical fleet operations (runner stop, fleet restart) use a two-step
inline confirmation UI instead of `window.confirm()`.

### 9.4 Token Handling
`GH_TOKEN` and `ANTHROPIC_API_KEY` must be supplied as environment variables
only — never hardcoded in source files or configuration. The recommended setup
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
| `author` | string | — | Filter by author login |
| `label` | string (repeatable) | — | Match any of these labels |
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

- `agent_claim` — extracted from any `claim:*` label on the PR.
- `linked_issues` — issue numbers found via `closes/fixes/resolves #N` in the PR body.
- `errors` — per-repo error messages; a failing repo does not abort the whole request.
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
| `label` | string (repeatable) | — | Match any of these labels |
| `assignee` | string | — | Filter by assignee login |
| `pickable_only` | bool | `false` | Only return issues available for agent pickup |
| `complexity` | string (repeatable) | — | Match any `complexity:*` value |
| `effort` | string (repeatable) | — | Match any `effort:*` value |
| `judgement` | string (repeatable) | — | Match any `judgement:*` value |
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

- `"force": true` — skip pickability enforcement (requires PRIVILEGED access).
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

## 15. Assistant Sidebar

### 15.1 Overview

A persistent collapsible sidebar that provides a conversational AI assistant
interface accessible from any tab in the dashboard.

### 15.2 Toggle

A button labelled "☰ Asst" in the header-right area toggles the sidebar
open or closed. The button is highlighted (blue background) when the sidebar
is open.

### 15.3 Layout

When open, the sidebar docks alongside the main content area in a flex row.
The user may configure it to dock to the left or right of the viewport.
The default position is right.

- Default width: 360px
- Draggable resize handle: 280px – 600px range
- The main content shrinks to fill the remaining width

### 15.4 Persistence

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

### 15.5 Conversation

Messages are displayed as chat bubbles. User messages dock right with a blue
background; assistant replies dock left with a tertiary background. Assistant
responses are rendered with a minimal inline Markdown renderer supporting
bold, italic, inline code, fenced code blocks, links, and ordered/unordered
lists — no external library required.

Input is a textarea. Enter sends the message; Shift+Enter inserts a newline.

### 15.6 API Integration

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

### 15.7 Settings

A gear icon in the sidebar header opens a settings card with:

- **Position**: radio buttons for Left / Right dock
- **Open by default**: checkbox
- **Include page context**: checkbox
- **Clear conversation**: destructive button that empties the transcript

### 15.8 Implementation

The `AssistantSidebar` component is defined in `frontend/index.html` just
before `QuickDispatchPopover`. It follows the no-JSX, no-build-step convention
of the rest of the frontend. Open/closed state is owned by the `App` component
and passed down as props; all other sidebar state is internal.
