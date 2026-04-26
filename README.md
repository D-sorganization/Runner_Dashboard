# D-sorganization Runner Dashboard

A web UI control surface for the D-sorganization self-hosted GitHub Actions
runner fleet. Monitor runner health in real-time, control runner lifecycle,
dispatch AI agents, manage workflows, and orchestrate multi-node deployments —
all from a single browser tab.

## Sibling repos

This is the **operator console** in a three-repo fleet. The cross-repo
contract is in
[`Repository_Management/docs/sibling-repos.md`](https://github.com/D-sorganization/Repository_Management/blob/main/docs/sibling-repos.md).

| Repo | Role |
| --- | --- |
| [`Repository_Management`](https://github.com/D-sorganization/Repository_Management) | Fleet orchestrator — CI workflows, skills, templates, agent coordination. |
| `runner-dashboard` (here) | Operator console — every dashboard tab and `/api/*` endpoint. |
| [`Maxwell-Daemon`](https://github.com/D-sorganization/Maxwell-Daemon) | Autonomous AI control plane consumed by the Maxwell tab over HTTP. |

The Maxwell tab calls Maxwell-Daemon over HTTP using the contract documented
in the sibling-repos doc. Maxwell-Daemon never calls back into the dashboard.

---


The dashboard is a local FastAPI server that proxies the GitHub API and exposes
system metrics. The frontend is a self-contained React SPA served directly as
a static HTML file — no build step, no npm, no node_modules.

## Security

> **Security Notice**: This dashboard provides full control over your GitHub
> Actions runner fleet. It has no built-in authentication. Restrict network
> access to trusted operators only. See [SECURITY.md](SECURITY.md) for the
> vulnerability disclosure policy.

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

```bash
git clone git@github.com:D-sorganization/runner-dashboard.git
cd runner-dashboard
export GITHUB_TOKEN=ghp_your_token_here
./start-dashboard.sh
```

Open http://localhost:8321 in your browser.

**Requirements:** Python 3.11+, a GitHub PAT with `repo` and `admin:org` scopes.

## Production Deployment

```bash
# Full setup on a fresh machine (installs systemd service)
bash deploy/setup.sh

# Update an existing deployment
bash deploy/update-deployed.sh
```

The dashboard runs as `runner-dashboard.service` on port 8321. Logs:
`journalctl -u runner-dashboard -f`

## Architecture

```
backend/     FastAPI server (Python 3.11+) — all /api/* routes
frontend/    Self-contained React SPA — index.html, no build step
deploy/      setup.sh, update-deployed.sh, systemd units, helpers
config/      Runtime config (agent_remediation.json, runner-schedule.json)
docs/        Documentation
```

The frontend uses `h()` (React.createElement alias) instead of JSX. All
dependencies are loaded from CDN. There is no build toolchain.

See [SPEC.md](SPEC.md) for the full API catalogue, configuration reference,
and architecture documentation.

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
