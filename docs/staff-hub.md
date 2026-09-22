# Staff Hub

Epic [#1192](https://github.com/D-sorganization/Runner_Dashboard/issues/1192).
The Staff Hub runs named AI "staff" roles (Night Watch, Issue Remediator,
Project Steward, …) as local CLI subprocesses on a dashboard node, records
every run, and streams the output to the operator console.

## Contract

| Concern | Owner | Mechanism |
| --- | --- | --- |
| Role definitions (`staff/roles/*.yml`) and playbooks (`docs/fleet-*.md`) | Repository_Management | Read by path (`STAFF_ROLES_DIR`, or the sibling checkout); never imported |
| Lease ritual (`check_agent_claim`, `post_agent_lease`, `agent_communicate`) | Repository_Management | Subprocess from `STAFF_RM_ROOT` or the sibling checkout |
| Run store, scheduler, board, stream, API | Runner Dashboard (`backend/staff/`, `backend/routers/staff.py`) | Node-local SQLite `staff_runs.sqlite3` under the config dir |
| Provider CLIs | The node | `claude`, `codex`, `agy`, `gemini`, `cursor-agent`, `ollama` on `PATH` |

## API (`/api/staff`)

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/api/staff/roster` | fleet peer | Roster: roles, provider availability, active counts |
| GET | `/api/staff/board` | fleet peer | This node's status monitor: running, queued, recent, spend today |
| GET | `/api/staff/runs` | fleet peer | History; filters `role`, `status`, `since`, `limit` |
| GET | `/api/staff/runs/{id}` | fleet peer | One run plus its events |
| GET | `/api/staff/runs/{id}/stream` | fleet peer | Server-sent events until the run ends |
| POST | `/api/staff/{role}/run` | orchestrator peer | Dispatch; `dry_run: true` returns the plan only |
| POST | `/api/staff/runs/{id}/cancel` | orchestrator peer | Terminate a run |

POST bodies need the CSRF sentinel header `X-Requested-With: XMLHttpRequest`
like every other dashboard POST. "Orchestrator peer" means an operator
principal, the `HUB_FLEET_TOKEN` bearer, or loopback with
`DASHBOARD_LOOPBACK_AUTH=1` (the same rule as `/api/orchestrator/*`).

Example dispatch:

```bash
curl -s -X POST http://127.0.0.1:8321/api/staff/night-watch/run \
  -H 'Authorization: Bearer $HUB_FLEET_TOKEN' -H 'X-Requested-With: XMLHttpRequest' \
  -H 'Content-Type: application/json' \
  -d '{"repo": "UpstreamDrift", "issue": 10622, "provider": "claude"}'
```

## Run lifecycle

`queued → preparing → running → succeeded | failed | cancelled`, or
`blocked` when `check_agent_claim` reports the issue is held by another agent.
`preparing` creates a git worktree under `STAFF_WORKTREES_ROOT`
(default `<repos root>/_staff_worktrees`) from `origin/main` on a
`staff/<role>-<target>-<id>` branch, cloning the repository bloblessly when no
local checkout exists. The prompt is the role instructions, the playbook path,
the target, and the fixed fleet rules (worktree only, TDD, draft PR, no merge).

## Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `STAFF_ROLES_DIR` | sibling `Repository_Management/staff/roles` | Role YAML directory |
| `STAFF_RUNS_DB` | `<config dir>/staff_runs.sqlite3` | Run store |
| `STAFF_REPOS_ROOT` | `~/Repositories`, `~/actions-runners/repos`, `/mnt/c/Users/<user>/Repositories` | Where checkouts live (`os.pathsep` list) |
| `STAFF_WORKTREES_ROOT` | `<first repos root>/_staff_worktrees` | Worktree location |
| `STAFF_RM_ROOT` | sibling `Repository_Management` | Lease ritual scripts |
| `STAFF_RM_PYTHON` | `python3` / `python` | Interpreter for the RM scripts |
| `STAFF_MAX_CONCURRENT_RUNS` | `3` | Runs executing at once on this node |
| `STAFF_RUN_TIMEOUT_SECONDS` | `14400` | Hard stop per run |

## Not yet here (tracked in the epic)

Hub fan-out of `/api/staff/board` across nodes (#1195), scheduler, windows,
holds and budgets (#1196), machine targeting (#1197), the Staff tab (#1198),
the Project Steward role and Projects tab (#1199), the usage ledger (#1200).
