# Dev Environment Runbook

Short reference for running the Runner Dashboard locally without polluting
your system Python.

## Quick start

```bash
./start-dashboard.sh
```

On first run this creates a project-local virtualenv at `./.venv/` and
installs `backend/requirements.txt` into it. The script never installs into
system site-packages and never uses `--break-system-packages`.

Open http://localhost:8321.

## Flags

| Flag        | Effect                                                                |
| ----------- | --------------------------------------------------------------------- |
| `--port N`  | Bind to port `N` (default `8321`).                                    |
| `--host H`  | Bind to host/interface `H` (default `127.0.0.1`).                     |
| `--bg`      | Run in the background, log to `/tmp/runner-dashboard.log`.            |
| `--reload`  | Hot-reload via `uvicorn --reload --reload-dir backend/`.              |
| `--mock`    | Sets `DASHBOARD_MOCK_MODE=1` for fixture-backed GH calls (wiring TODO).|
| `--help`    | Print usage and exit.                                                 |

## Virtualenv location

- Path: `./.venv/`
- Stamp: `./.venv/.installed-stamp` — `pip install` only re-runs when
  `backend/requirements.txt` is newer than this stamp.
- To force a clean install: `rm -rf .venv && ./start-dashboard.sh`.

## Make targets

| Target      | What it runs                          |
| ----------- | ------------------------------------- |
| `make dev`  | `./start-dashboard.sh --reload`        |
| `make test` | `pytest tests/ -q --tb=short`          |
| `make seed` | Stub — prints a TODO until fixtures land. |

## Stopping

```bash
./stop-dashboard.sh
```

Or, if started with `--bg`, `kill` the PID printed at startup.
