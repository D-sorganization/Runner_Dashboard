# Staff Hub

Epic [#1192](https://github.com/D-sorganization/Runner_Dashboard/issues/1192).
The Staff Hub runs named AI "staff" roles (Night Watch, Issue Remediator,
Project Steward, …) as local CLI subprocesses on a dashboard node, records
every run, and streams the output to the operator console.

## Contract

| Concern                                                                     | Owner                                                           | Mechanism                                                                 |
| --------------------------------------------------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------- |
| Role definitions (`staff/roles/*.yml`) and playbooks (`docs/fleet-*.md`)    | Repository_Management                                           | Read by path (`STAFF_ROLES_DIR`, or the sibling checkout); never imported |
| Lease ritual (`check_agent_claim`, `post_agent_lease`, `agent_communicate`) | Repository_Management                                           | Subprocess from `STAFF_RM_ROOT` or the sibling checkout                   |
| Run store, scheduler, board, stream, API                                    | Runner Dashboard (`backend/staff/`, `backend/routers/staff.py`) | Node-local SQLite `staff_runs.sqlite3` under the config dir               |
| Provider CLIs                                                               | The node                                                        | `claude`, `codex`, `agy`, `gemini`, `cursor-agent`, `ollama` on `PATH`    |

## API (`/api/staff`)

| Method | Path                          | Auth              | Purpose                                                               |
| ------ | ----------------------------- | ----------------- | --------------------------------------------------------------------- |
| GET    | `/api/staff/roster`           | fleet peer        | Roster: roles, provider availability, active counts                   |
| GET | `/api/staff/board` | fleet peer | Status monitor. Fleet-wide when peers are configured (`machines`, `online`, `offline`, merged `running`/`queued`/spend, `liveness_alerts`); `?local=1` returns this node only (with its `liveness` list) |
| GET | `/api/staff/summary` | fleet peer | One-call brief for Barb/Orchestrator: `in_flight`, `attention` (failed/blocked 24 h), `recent_24h`, `spend_today_usd`, `providers` per machine, `holds`, `liveness_alerts`, `roles` |
| GET    | `/api/staff/runs`             | fleet peer        | History; filters `role`, `status`, `since`, `limit`                   |
| GET    | `/api/staff/runs/{id}`        | fleet peer        | One run plus its events                                               |
| GET    | `/api/staff/runs/{id}/stream` | fleet peer        | Server-sent events until the run ends                                 |
| POST   | `/api/staff/{role}/run`       | orchestrator peer | Dispatch; `dry_run: true` returns the plan only                       |
| POST   | `/api/staff/runs/{id}/cancel` | orchestrator peer | Terminate a run                                                       |
| GET    | `/api/staff/schedule`         | fleet peer        | Per role: next fire, in window now, blocking hold, budget, last fired |
| GET    | `/api/staff/holds`            | fleet peer        | The holds list                                                        |
| PUT    | `/api/staff/holds`            | orchestrator peer | Replace the holds list                                                |
| GET | `/api/staff/usage` | fleet peer | Cost and token usage grouped by `provider`, `role` or `day` (`?group=`, `?since=`), with totals and the daily budget percent |
| GET | `/api/staff/usage/pricing` | fleet peer | The price table used for estimates |
| POST | `/api/staff/usage/export` | orchestrator peer | Append today's per-provider totals to Repository_Management `data/credit_usage.json` via `scripts/append_credit_usage.py` (503 without an RM checkout) |

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

## Machine targeting

`machine` in the dispatch body is `local` (default; also this host's own
name), a peer name from `FLEET_NODES` or the machine registry (case
insensitive), or `auto`. `auto` fetches every peer's local board and picks the
online node with the fewest active runs that has the provider installed, ties
going to the local node; if no peer answers the run stays local. A dispatch
aimed at a peer is forwarded to that node's `/api/staff/{role}/run` with the
`HUB_FLEET_TOKEN` bearer and returns the node's answer plus `forwarded_to`.
An unreachable peer is a 503; a peer that rejects the request passes its
status and detail through. Unknown names are a 422 listing the known machines.

## Run lifecycle

`queued → preparing → running → succeeded | failed | cancelled`, or
`blocked` when `check_agent_claim` reports the issue is held by another agent.
`preparing` creates a git worktree under `STAFF_WORKTREES_ROOT`
(default `<repos root>/_staff_worktrees`) from `origin/main` on a
`staff/<role>-<target>-<id>` branch, cloning the repository bloblessly when no
local checkout exists. The prompt is the role instructions, the playbook path,
the target, and the fixed fleet rules (worktree only, TDD, draft PR, no merge).

## Scheduling, holds and budgets (#1196)

`backend/staff/scheduler.py` runs an in-process ticker (default every 30 s,
started from server startup next to the other background loops, gated by
`STAFF_SCHEDULER_ENABLED`). For each dispatchable role with a `schedule`
(five-field cron, `America/Los_Angeles`, parsed by `staff/schedule.py` with
no third-party dependency) it computes the next slot after the role's cursor
and, once that slot has passed, submits `RunRequest(role, repo=<first repo>,
prompt="Scheduled run", requested_by="scheduler")` when every gate opens:

1. **Window** — `window: {start, end}` in role YAML, wall clock in LA; `start >
end` is overnight (`22:00`–`06:00`), start inclusive, end exclusive, empty
   means always open.
2. **Holds** — no active hold in the holds list matches the role (`applies_to`
   contains the role name, `*`, or `repo:<name>` for the role's first repo).
3. **One run per role** — no queued/preparing/running run for the role.
4. **Budget** — `staff/budget.py` compares today's spend for the role (local
   midnight, `RunStore.spend_by_role_since`) with `budget.usd_per_day`; a run
   is refused when the cap is reached or when `usd_per_run` would push past
   it. Alerts log at 75 %, 90 % and 100 % of the cap, once per threshold per
   role with a six-hour debounce (in memory).

A handled slot moves the role's cursor to _now_ whether it fired or was
skipped, so a node that was asleep fires at most once when it wakes and a slot
skipped for a hold is not retried until the next slot. Cursor, last fire time
and last reason persist in `<config dir>/staff_schedule_state.json`.

The **holds list** (`staff/holds.py`) is `<config dir>/staff_holds.json`,
seeded on first load from the `holds:` lists of the role YAML (same text on
several roles merges into one hold). Each hold is `{id, text, set_on,
lifted_when, applies_to, active}`; `PUT /api/staff/holds` replaces the whole
list. Lifting a hold means `active: false` (or removing it).

## Liveness (#1209)

The Codex desktop sweeps stopped on 2026-05-27 and nobody noticed for four
months because nothing watched "when did this job last succeed". The Staff
Hub watches. `backend/staff/liveness.py` derives, for every dispatchable role
with a `schedule`, one row in the node's board `liveness` list:

| Field | Meaning |
| --- | --- |
| `last_success` | `ended_at` of the latest `succeeded` run for the role in this node's store |
| `last_attempt` | `created_at` of the latest run of any status |
| `last_fired` | the scheduler's `last_fired` for the role (`staff_schedule_state.json`) |
| `next_fire`, `expected_interval_seconds` | next slot after now, and the gap to the slot after that |
| `status` | `ok` · `late` · `dead` · `never` |

**How to read the badge.** `ok`: the last success is younger than 1.5
intervals. `late`: older than 1.5 intervals (one missed slot plus slack).
`dead`: older than 3 intervals, or the scheduler has fired the role (or a run
exists) and it has never succeeded — a still-running first attempt shows as
`late`. `never`: no run and no fire yet, which is normal for a freshly added
role until its first slot. Only `late` and `dead` rows are alerts.

The hub board (`GET /api/staff/board` with peers) keeps each node's list under
`machines[<name>].liveness` and lifts the late/dead rows of every **online**
node, tagged with `machine`, into `liveness_alerts`; `GET /api/staff/summary`
carries the same `liveness_alerts`. The Staff tab's Board panel shows them as
a warning list above the machines. No GitHub call is involved: stopping the
scheduler on one node turns that node's roles `late` then `dead` on the hub
from the stores alone.

A role turning `dead` also records a `staff_role_dead` fleet event (severity
`warning`, `node` = the machine) in the fleet event log, once per role per six
hours (in-memory debounce, evaluated whenever the local board is built).

**What to do.** `late`: check `GET /api/staff/schedule` for the blocker
(`outside run window`, a hold, an active run, budget) and `/api/staff/runs?role=`
for the last failure. `dead`: the scheduler on that node is not running
(`STAFF_SCHEDULER_ENABLED`, service down) or every attempt fails — read the
last run's events, fix, then dispatch the role by hand; the row returns to
`ok` on the next success.

## PR consolidation (#1213)

Owner decision relayed 2026-09-22 (RM#1690): when a repository has many open
PRs the fleet folds them into **one PR per repository** instead of draining
them one at a time. A role opts in through its YAML:

```yaml
strategy:
  consolidate_when:
    open_prs: 6          # repo's open non-draft PRs
    utilisation_pct: 70  # fleet runners busy / online
```

`backend/staff/consolidation.py` evaluates the block whenever a run for the
role has a repository — in the scheduler tick and in `POST /api/staff/{role}/run`
(dry run and real dispatch alike). Inputs: the open non-draft PR count from
`gh api --paginate /repos/<org>/<repo>/pulls?state=open` and the utilisation
`busy_runners / online_runners` from the capacity provider the Conductor gate
uses (`orchestrator_api`). The mode is `consolidate` when **every** configured
threshold is met (a key left out is always met) and `serial` otherwise; if
either input cannot be fetched the mode is `serial` with the reason
`inputs unavailable`, so a GitHub outage never blocks a slot.

The decision is injected into the prompt as one paragraph
(`Consolidation mode: consolidate — <reason>. Fold the eligible open PRs of this
repository into ONE PR; exclusions: draft, do-not-merge, do-not-automate,
claim:local, PRs of other live sessions, workflow changes, bot snapshot PRs
that delete main lines. Never cancel or re-run other PRs' CI.`, or the serial
variant) and surfaces as:

| Where | Field |
| --- | --- |
| `GET /api/staff/roster` | `roles[].strategy` (the raw block; the Roster card shows the threshold) |
| `POST /api/staff/{role}/run` with `dry_run` | `plan.consolidation` = `{mode, reason, threshold}` (the Assign preview shows it) |
| run records | `strategy_mode` (`consolidate` / `serial` / empty when not applicable) |
| run records | `outcome`, e.g. `consolidated 12 PRs into #1801`, parsed case-insensitively from the final `STAFF_RESULT:` line (`consolidated N PRs into #M`); shown in the run log and run detail |

Both run columns are additive (`PRAGMA`-guarded `ALTER TABLE`, like
`cost_method`); older nodes simply omit them.

## Environment

| Variable                    | Default                                                                         | Meaning                                              |
| --------------------------- | ------------------------------------------------------------------------------- | ---------------------------------------------------- |
| `STAFF_SCHEDULER_ENABLED`   | `1`                                                                             | `0`/`false` keeps the scheduler thread from starting |
| `STAFF_HOLDS_FILE`          | `<config dir>/staff_holds.json`                                                 | Holds list                                           |
| `STAFF_SCHEDULE_STATE`      | `<config dir>/staff_schedule_state.json`                                        | Per-role cursor / last fired                         |
| `STAFF_ROLES_DIR`           | sibling `Repository_Management/staff/roles`                                     | Role YAML directory                                  |
| `STAFF_RUNS_DB`             | `<config dir>/staff_runs.sqlite3`                                               | Run store                                            |
| `STAFF_REPOS_ROOT`          | `~/Repositories`, `~/actions-runners/repos`, `/mnt/c/Users/<user>/Repositories` | Where checkouts live (`os.pathsep` list)             |
| `STAFF_WORKTREES_ROOT`      | `<first repos root>/_staff_worktrees`                                           | Worktree location                                    |
| `STAFF_RM_ROOT`             | sibling `Repository_Management`                                                 | Lease ritual scripts                                 |
| `STAFF_RM_PYTHON`           | `python3` / `python`                                                            | Interpreter for the RM scripts                       |
| `STAFF_MAX_CONCURRENT_RUNS` | `3`                                                                             | Runs executing at once on this node                  |
| `STAFF_RUN_TIMEOUT_SECONDS` | `14400`                                                                         | Hard stop per run                                    |
| `STAFF_PEER_TIMEOUT_SECONDS` | `6` | Per-peer timeout for board fan-out (forwarded dispatches allow 5×) |
| `STAFF_BUDGET_USD_PER_DAY` | `0` (unlimited) | Fleet-wide daily ceiling reported by `/api/staff/usage` |
| `STAFF_WALL_USD_PER_MIN` | unset | `provider=rate,...` wall-time fallback for providers without token accounting |

## Usage and cost (#1200)

Every finished run gets a `cost_usd` and a `cost_method`: `reported` when the
CLI emitted cost (Claude stream-json), `token_table` from `backend/staff/pricing.py`
when only tokens are known, `wall_time` from `STAFF_WALL_USD_PER_MIN`, else
`none` (Codex plain-text output until `--json` is adopted; Ollama is free).
`GET /api/staff/usage` aggregates the store by provider, role or day and reports
the `STAFF_BUDGET_USD_PER_DAY` ceiling; `POST /api/staff/usage/export` appends
today's totals to the Repository_Management credit ledger.

## Not yet here (tracked in the epic)

The fleet deploy (#1201).