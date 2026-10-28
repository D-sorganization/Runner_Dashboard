# D-sorganization Runner Dashboard

Runner_Dashboard is a self-hosted workspace that carries engineering work from
an agent session through GitHub review and CI, keeping task context and evidence
with the work. It brings runner operations, agent dispatch, and workflow status
into one local console.

Unlike hosted agent workspaces, it runs on your own infrastructure and focuses
on fleet and GitHub workflow integration; it is not a hosted coding environment.
Unlike a runner-only dashboard, it also connects agent runs and their evidence
to the review workflow. Capacity-aware scheduling is not a positioning claim
until a benchmark supports it.

## Sibling repos

This is the **operator console** in a three-repo fleet. The cross-repo
contract is in
[`Repository_Management/docs/sibling-repos.md`](https://github.com/D-sorganization/Repository_Management/blob/main/docs/sibling-repos.md).

| Repo                                                                                | Role                                                                      |
| ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| [`Repository_Management`](https://github.com/D-sorganization/Repository_Management) | Fleet orchestrator — CI workflows, skills, templates, agent coordination. |
| `runner-dashboard` (here)                                                           | Operator console — every dashboard tab and `/api/*` endpoint.             |
| [`Maxwell-Daemon`](https://github.com/D-sorganization/Maxwell-Daemon)               | Autonomous AI control plane consumed by the Maxwell tab over HTTP.        |

The Maxwell tab calls Maxwell-Daemon over HTTP using the contract documented
in the sibling-repos doc. Maxwell-Daemon never calls back into the dashboard.

## Capability matrix

The core repo runs **standalone** by default; a private **overlay** (see
[`docs/architecture/overlay-contract.md`](docs/architecture/overlay-contract.md))
supplies operator-specific config without the core ever naming private hosts,
people, or paths.

| Capability                                      | Standalone (no overlay) | With overlay (`RD_OVERLAY_PATH` set) |
| ------------------------------------------------ | ------------------------ | ------------------------------------- |
| Dashboard UI, API, auth, sessions                | ✅ full                  | ✅ full                               |
| Runner monitoring and lifecycle control          | ✅ full                  | ✅ full                               |
| Named self-hosted hosts / tailnet addresses      | — (operator supplies via env/config directly) | ✅ sourced from the overlay |
| Agent token file paths, per-agent config          | — (operator supplies directly)                | ✅ sourced from the overlay |
| Private roles, policy, and internal docs          | — (not present)                                | ✅ sourced from the overlay |

Until `RD_OVERLAY_PATH` is set, behaviour is unchanged from today; setting it
loads and validates the overlay at startup and the core refuses to start on an
invalid overlay rather than running with partial private config.

## Architecture

- **C4 Architecture Map**: See [docs/architecture/C4.md](docs/architecture/C4.md) for system context, container boundaries, and feature mappings.

---

The dashboard is a local FastAPI server that proxies the GitHub API and exposes
system metrics. The frontend is a Vite-built React + TypeScript SPA: source
lives in `frontend/src/`, and `vite build` produces a static bundle that the
backend serves as assets.

## Security

> **Security Notice**: This dashboard provides full control over your GitHub
> Actions runner fleet. Treat the network it binds to as a trust boundary and
> restrict access to trusted operators. See [SECURITY.md](SECURITY.md) for the
> vulnerability disclosure policy.

The dashboard ships with a built-in identity and authorization stack — it is
**not** an unauthenticated service. Configure it before exposing the port:

- **Principals, roles, and scopes** — every `/api/*` route is authenticated by
  default (a structural perimeter rejects unauthenticated requests); operations
  are gated by role-derived scopes. See `backend/identity.py`
  (`require_principal`, `require_scope`, `SCOPE_PRESETS`).
- **Sessions with revocation** — server-side sessions backed by
  `backend/session_management.py`; revoked/expired sessions are rejected.
- **GitHub OAuth + org membership check** — operators sign in via GitHub and are
  admitted only if they belong to `GITHUB_ORG`, the configured organization
  (required when OAuth sign-in is enabled)
  (`backend/routers/auth.py`).
- **WebAuthn** — passkey registration/assertion scaffolding in
  `backend/auth_webauthn.py`.
- **Service tokens / API-key bootstrap** — bot principals authenticate with
  minted service tokens; see `backend/server.py` and the admin token routes.
- **Intra-fleet auth** — hub-reachable fleet routes validate `HUB_FLEET_TOKEN`;
  see [`docs/runbooks/hub-credentials.md`](docs/runbooks/hub-credentials.md).

`require_principal` **fails closed**: a request with no valid credential gets
`401`. A local development bypass exists only when `DASHBOARD_LOOPBACK_AUTH=1`
and the peer is loopback. Network isolation is a defense-in-depth layer on top
of this stack, not a replacement for it.

## Features

- **Fleet Tab** — Real-time runner status (idle/active/offline), per-runner
  start/stop controls, bulk fleet actions
- **History Tab** — Paginated workflow run history across all org repos with
  rerun/cancel support
- **Queue Tab** — Live job queue with diagnostic tooling to explain stalls
- **Queue Health Panel** — Scans all org repos for stale queued runs (jobs
  that will never execute because runners are offline or labels have changed),
  shows age/repo/workflow, and bulk-cancels in one click. Also available as
  `/api/queue/stale` (GET) and `/api/queue/purge-stale` (POST). Auto-runs
  hourly via `deploy/scheduled-dashboard-maintenance.sh`.
- **Machines Tab** — Multi-node hardware inventory with live CPU/RAM/disk/GPU
  metrics
- **Organization Tab** — Org-level runner groups, labels, and aggregate health
- **Tests Tab** — Dispatch and monitor heavy integration test runs
- **Stats Tab** — Workflow success rates, duration trends, per-repo breakdowns
- **Reports Tab** — Dated fleet report viewer with metrics summary cards
- **Scheduled Workflows Tab** — Cron schedule inventory with manual dispatch
- **Runner Plan Tab** — Autoscaler configuration and schedule-based scaling
- **Local Apps Tab** — Health monitoring for registered local processes
- **Remediation Tab** — AI agent dispatch (Jules, GAAI, Claude, Codex) with
  plan history
- **Workflows Tab** — Browse and manually dispatch any org workflow
- **Credentials Tab** — Read-only secrets/variables inventory for audit
- **Assessments Tab** — Code quality assessment dispatch and score tracking
- **Feature Requests Tab** — Feature request templates and implementation dispatch
- **Maxwell Tab** — Maxwell daemon control (fleet orchestration AI)
- **Fleet Orchestration Tab** — Cross-node deployment orchestration
- **Help Tab** — In-app AI-powered help chat

## Quick Start

### Try the Offline Demo

The demo starts with fictional sample runners and workflow runs. It needs no
GitHub credentials, makes no GitHub requests, and disables API access:

```bash
git clone https://github.com/D-sorganization/Runner_Dashboard.git
cd Runner_Dashboard
bash start-dashboard.sh --demo
```

Open http://localhost:8321. The page is marked **DEMO DATA · READ ONLY**.
Python 3.11+, Node.js 18+, and npm are required for a clean checkout.

### Connect a Real Dashboard

```bash
git clone git@github.com:D-sorganization/runner-dashboard.git
cd runner-dashboard
export GITHUB_TOKEN=ghp_your_token_here
./start-dashboard.sh
```

Open http://localhost:8321 in your browser.

On a clean checkout `start-dashboard.sh` provisions a project-local `.venv` from
the repo-root `requirements.txt` and builds the frontend bundle
(`frontend/dist/`) via `npm ci && npm run build`. It exits non-zero with
instructions if the dependencies or a working `npm` are unavailable, rather than
starting a broken server.

**Requirements:** Python 3.11+, Node.js 18+/npm (for the frontend build), and a
GitHub PAT with `repo` and `admin:org` scopes. Use the offline demo above to
explore the interface without credentials.

### Cold-start check

After the demo, ask someone outside the fleet to follow this Quick Start from a
clean machine. Record any prerequisite, instruction, or first-run step that
blocks them, then file each friction point as an issue. This check is not yet
complete.

### Deterministic / offline-constrained installs

`npm ci` is expected to succeed from the committed `package-lock.json` alone,
with no network access, on any fleet machine. Run
`npm run verify-lockfile` (or `bash scripts/verify-lockfile.sh`) to check
this locally: it does a clean-state `npm ci`, confirms `npm ls` reports no
unmet/invalid entries, and runs `npm run build`.

If a machine's local npm cache is not yet warm (e.g. a WAN-constrained
fleet host being provisioned for the first time), prime it once from a
machine that already has a complete, working `node_modules` for this
lockfile:

```bash
# On the healthy/reference machine (with a durable npm cache):
npm cache verify

# Copy that machine's npm cache directory (see `npm config get cache`) to
# the constrained machine, or run `npm ci` there first over a working
# network link. Once the cache is warm, `npm ci --prefer-offline` (or
# plain `npm ci`) will succeed without depending on registry latency.
```

This priming step is a manual workaround, not an automated offline mirror.
A scripted/versioned offline artifact pipeline (build once on `main`,
publish a checksummed `dist/` artifact, let `deploy/update-deployed.sh`
install from that artifact without touching npm at all) is tracked as a
larger follow-up in issue #1085 and is out of scope for this lockfile fix.

## Production Deployment

```bash
# Full setup on a fresh machine (installs systemd service)
bash deploy/setup.sh

# Update an existing deployment
bash deploy/update-deployed.sh
```

Runner Dashboard single-worker is the only supported mode. Keep `WORKERS=1`
and `uvicorn --workers 1` (including `WEB_CONCURRENCY=1` when set). Startup
refuses worker counts greater than one because leases, locks, rate limiters,
bearer tokens and event sequences are process-local. Multi-worker support
requires a separate shared-state redesign.

The dashboard runs as `runner-dashboard.service` on port 8321. Logs:
`journalctl -u runner-dashboard -f`

## Architecture

```
backend/     FastAPI server (Python 3.11+) — all /api/* routes
frontend/    Vite-built React + TypeScript SPA — src/ source, vite build output
deploy/      setup.sh, update-deployed.sh, systemd units, helpers
config/      Runtime config (agent_remediation.json, runner-schedule.json)
docs/        Documentation
```

The frontend is written in JSX/TSX (React + TypeScript). Dependencies are
managed via `package.json` and installed with `npm`; the bundle is produced
by the Vite build toolchain.

See [SPEC.md](SPEC.md) for the full API catalogue, configuration reference,
and architecture documentation.

## Multi-Instance (Hub vs Node) State

When deployed across multiple machines, the dashboard differentiates between "Hub" and "Node" operation:

- **Hub (Leader)**: The central dashboard instance. Processes global actions, scheduled background tasks, stale queue cleanup, and autoscaler evaluations. Each instance runs a single worker; leader-election coordinates background tasks between instances.
- **Node (Follower)**: Run instances on edge runner hosts without the leader lock (`DASHBOARD_LEADER=0`). They provide the local `/api/system/metrics` endpoints that the Hub aggregates, and stream logs/metrics to the Hub.
  If two Hubs run concurrently without a shared lock directory, background scans (like queue purging) will run twice, but API operations remain idempotent.

## Development

```bash
# Lint
ruff check backend/

# Format
ruff format backend/

# Type check
mypy backend/ --ignore-missing-imports

# Tests
pytest tests/ -q
```

CI runs automatically on every PR via the `quality-gate` and `Verify SPEC.md
freshness` checks. The d-sorg-fleet self-hosted runners execute CI jobs.

## Contributing

1. Read [CLAUDE.md](CLAUDE.md) for agent coordination and coding conventions.
2. Post a coordination lease on the issue before starting work.
3. Open a PR targeting `main`.
4. Update `SPEC.md` if your PR changes documented behavior.
5. Ensure CI passes before requesting review.

Branch protection requires the `quality-gate` check to pass. PRs do not
require human review (review count = 0) but all automated checks must be green.
