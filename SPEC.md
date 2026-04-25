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
AI agent dispatch control panel. Configures and dispatches remediation plans
to Jules, GAAI, Claude, or Codex agents. Shows dispatch history and plan
status. Supports per-repo agent routing.

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
