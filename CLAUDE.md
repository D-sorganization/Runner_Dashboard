# CLAUDE.md — Runner Dashboard

Quick-reference for developers and AI agents working in this repository.

## Multi-Agent Coordination

Before starting work on any issue, agents must acquire a coordination lease to
prevent redundant parallel work.

**Lease protocol (required for all agents):**

1. Read `docs/agent-coordination-strategy.md` for full protocol details.
2. Post a lease comment on the issue before beginning work:
   ```
   lease: <agent-id> expires <ISO-8601 timestamp +2h>
   ```
3. Renew the lease every 2 hours if work is ongoing.
4. Release the lease (or let it expire) when the PR is opened or work is abandoned.

The Agent Lease Reaper workflow sweeps expired leases every 30 minutes.
`claim:<agent>` labels are automatically removed when leases expire without an
open PR.

**Agent priority order** (highest wins redundancy conflicts):
`user > maxwell-daemon > claude > codex > jules > local > gaai`

## Project Overview

`runner-dashboard` is the web UI control surface for the D-sorganization
self-hosted GitHub Actions runner fleet. It provides real-time monitoring,
runner lifecycle control, AI agent dispatch, workflow management, and fleet
orchestration from a single browser tab.

The dashboard runs as a local FastAPI server on port 8321 and serves a
self-contained React SPA with no build step required.

## Architecture

```
runner-dashboard/
├── backend/            FastAPI server (Python 3.11+)
│   ├── server.py           Main application (~5000 lines, all /api/* routes)
│   ├── agent_remediation.py    AI agent dispatch and remediation logic
│   ├── dispatch_contract.py    Workflow dispatch type contracts
│   ├── machine_registry.py     Multi-node fleet registry
│   ├── machine_registry.yml    Fleet node definitions
│   ├── scheduled_workflows.py  Scheduled workflow inventory
│   ├── deployment_drift.py     Deployment version drift detection
│   ├── local_app_monitoring.py Local process health monitoring
│   ├── usage_monitoring.py     Runner usage metrics
│   ├── workflow_stats.py       Workflow statistics aggregation
│   ├── report_files.py         Report parsing utilities
│   ├── runner_autoscaler.py    Dynamic runner scaling logic
│   └── requirements.txt        Python dependencies
├── frontend/           Self-contained React SPA (no build step)
│   ├── index.html          Single-file app (~11k lines)
│   ├── RunnerDashboard.jsx Exported component (reference copy)
│   ├── manifest.webmanifest PWA manifest
│   └── icon.svg            App icon
├── deploy/             Deployment and operations scripts
│   ├── setup.sh            Full machine setup (installs runners, service, etc.)
│   ├── update-deployed.sh  Pull latest and restart service
│   ├── runner-dashboard.service  systemd unit file
│   ├── runner-autoscaler.service Autoscaler systemd unit
│   ├── runner-scheduler.py     Cron-style runner schedule daemon
│   └── ...                 Other helper scripts
├── config/             Runtime configuration
│   ├── agent_remediation.json  Remediation settings
│   ├── runner-schedule.json    Runner on/off schedule
│   └── usage_sources.json      Usage data source definitions
├── docs/               Documentation
├── start-dashboard.sh  Quick local start script
├── stop-dashboard.sh   Quick local stop script
├── local_apps.json     Local application registry
├── VERSION             Semantic version file
├── SPEC.md             Authoritative specification
└── CLAUDE.md           This file
```

## Dev Commands

### Run locally

```bash
# Quick start (installs deps in venv, starts server on :8321)
./start-dashboard.sh

# Stop
./stop-dashboard.sh

# Manual start
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python server.py
```

Open http://localhost:8321 in your browser.

### Lint and format

```bash
# Lint Python
ruff check backend/

# Format Python
ruff format backend/

# Auto-fix lint issues
ruff check backend/ --fix
```

### Type check

```bash
pip install mypy types-PyYAML types-requests
mypy backend/ --ignore-missing-imports
```

### Security scan

```bash
pip install bandit
bandit -r backend/ -ll -ii
```

### Tests

```bash
pip install pytest pytest-cov
pytest tests/ -q --tb=short
```

### Pre-commit hooks

```bash
pip install pre-commit
pre-commit install
pre-commit install --hook-type pre-push
pre-commit run --all-files
```

## CI/CD

All PRs run the **CI Standard** workflow (`ci-standard.yml`), which enforces:

- `quality-gate`: ruff lint, ruff format, mypy type check, no placeholders,
  pip-audit (non-blocking)
- `security-scan`: pip-audit on requirements.txt
- `tests`: pytest suite (after quality-gate passes)

PRs also run **Spec Check** (`ci-spec-check.yml`) — if backend source files
change without a SPEC.md update, the check fails. Apply `spec-exempt` label
to bypass.

Agent workflows:
- **Jules Control Tower** — orchestrates CI remediation and weekly maintenance
- **Jules PR AutoFix** — iteratively fixes CI failures by pushing to PR branches
- **Jules Auto-Repair** — worker called by Control Tower for complex repairs
- **Agent Redundant PR Closer** — closes duplicate agent PRs by priority
- **Agent Lease Reaper** — sweeps expired coordination leases every 30 min
- **Agent Fleet Dashboard** — regenerates `docs/fleet-in-flight.md` every 15 min

## Coding Conventions

### Python (backend/)

- Python 3.11+ only. Use `match`/`case`, `X | Y` unions, `datetime.UTC`.
- All public functions must have full type annotations.
- Use `logging` module (via `log = logging.getLogger("dashboard")`). Never `print()`.
- Prefer `pathlib.Path` over `os.path`.
- Keep functions focused. Functions over ~50 lines are candidates for extraction.
- Imports: stdlib → third-party → local, sorted within groups (ruff enforces).
- No wildcard imports (`from module import *`).
- No debug statements (`breakpoint()`, `import pdb`).
- Constants in `UPPER_SNAKE_CASE` at module level.
- FastAPI route handlers should be async where I/O is involved.

### Frontend (frontend/)

- **No build step.** The frontend is a single `index.html` served directly.
- **No JSX.** Use `h()` (aliased from `React.createElement`) for all elements.
- **No npm/node_modules.** All dependencies loaded from CDN via `<script>` tags.
- State managed with React hooks (`useState`, `useEffect`, `useCallback`).
- Tabs are top-level components defined in the single file.
- Styling via inline styles and a minimal CSS block in `<style>`.
- No TypeScript — plain JavaScript with JSDoc comments for complex types.

### General

- Branch names: `feat/`, `fix/`, `chore/`, `docs/` prefixes.
- Commit messages follow Conventional Commits (`feat:`, `fix:`, `chore:`, etc.).
- Update `SPEC.md` when adding or changing documented behavior.
- Update `VERSION` (semver) on meaningful releases.
