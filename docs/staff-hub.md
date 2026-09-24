# Staff Hub

Epic [#1192](https://github.com/D-sorganization/Runner_Dashboard/issues/1192).
The Staff Hub runs named AI "staff" roles (Night Watch, Issue Remediator,
Project Steward, …) as local CLI subprocesses on a dashboard node, records
every run, and streams the output to the operator console.

## Contract

| Concern                                                                     | Owner                                                           | Mechanism                                                                                                   |
| --------------------------------------------------------------------------- | --------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Role definitions (`staff/roles/*.yml`) and playbooks (`docs/fleet-*.md`)    | Repository_Management                                           | Read by path (`STAFF_ROLES_DIR`, or the sibling checkout); never imported                                   |
| Lease ritual (`check_agent_claim`, `post_agent_lease`, `agent_communicate`) | Repository_Management                                           | Subprocess from `STAFF_RM_ROOT` or the sibling checkout                                                     |
| Run store, scheduler, board, stream, API                                    | Runner Dashboard (`backend/staff/`, `backend/routers/staff.py`) | Node-local SQLite `staff_runs.sqlite3` under the config dir                                                 |
| Provider CLIs                                                               | The node                                                        | `claude`, `codex`, `agy`, `gemini`, `cursor-agent` on `PATH`; an Ollama server for `ollama`/`claude-ollama` |

## API (`/api/staff`)

| Method | Path                          | Auth              | Purpose                                                                                                                                                                                                  |
| ------ | ----------------------------- | ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/staff/roster`           | fleet peer        | Roster: roles, provider availability, active counts                                                                                                                                                      |
| GET    | `/api/staff/board`            | fleet peer        | Status monitor. Fleet-wide when peers are configured (`machines`, `online`, `offline`, merged `running`/`queued`/spend, `liveness_alerts`); `?local=1` returns this node only (with its `liveness` list) |
| GET    | `/api/staff/summary`          | fleet peer        | One-call brief for Barb/Orchestrator: `in_flight`, `attention` (failed/blocked 24 h), `recent_24h`, `spend_today_usd`, `providers` per machine, `holds`, `liveness_alerts`, `roles`                      |
| GET    | `/api/staff/runs`             | fleet peer        | History; filters `role`, `status`, `since`, `limit`                                                                                                                                                      |
| GET    | `/api/staff/runs/{id}`        | fleet peer        | One run plus its events                                                                                                                                                                                  |
| GET    | `/api/staff/runs/{id}/stream` | fleet peer        | Server-sent events until the run ends                                                                                                                                                                    |
| POST   | `/api/staff/{role}/run`       | orchestrator peer | Dispatch; `dry_run: true` returns the plan only                                                                                                                                                          |
| POST   | `/api/staff/runs/{id}/cancel` | orchestrator peer | Terminate a run                                                                                                                                                                                          |
| GET    | `/api/staff/schedule`         | fleet peer        | Per role: next fire, in window now, blocking hold, budget, last fired                                                                                                                                    |
| GET    | `/api/staff/holds`            | fleet peer        | The holds list                                                                                                                                                                                           |
| PUT    | `/api/staff/holds`            | orchestrator peer | Replace the holds list                                                                                                                                                                                   |
| GET    | `/api/staff/usage`            | fleet peer        | Cost and token usage grouped by `provider`, `role` or `day` (`?group=`, `?since=`), with totals and the daily budget percent                                                                             |
| GET    | `/api/staff/usage/pricing`    | fleet peer        | The price table used for estimates                                                                                                                                                                       |
| POST   | `/api/staff/usage/export`     | orchestrator peer | Append today's per-provider totals to Repository_Management `data/credit_usage.json` via `scripts/append_credit_usage.py` (503 without an RM checkout)                                                   |

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

| Field                                    | Meaning                                                                    |
| ---------------------------------------- | -------------------------------------------------------------------------- |
| `last_success`                           | `ended_at` of the latest `succeeded` run for the role in this node's store |
| `last_attempt`                           | `created_at` of the latest run of any status                               |
| `last_fired`                             | the scheduler's `last_fired` for the role (`staff_schedule_state.json`)    |
| `next_fire`, `expected_interval_seconds` | next slot after now, and the gap to the slot after that                    |
| `status`                                 | `ok` · `late` · `dead` · `never`                                           |

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
    open_prs: 6 # repo's open non-draft PRs
    utilisation_pct: 70 # fleet runners busy / online
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

| Where                                       | Field                                                                                                                                                                               |
| ------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/staff/roster`                     | `roles[].strategy` (the raw block; the Roster card shows the threshold)                                                                                                             |
| `POST /api/staff/{role}/run` with `dry_run` | `plan.consolidation` = `{mode, reason, threshold}` (the Assign preview shows it)                                                                                                    |
| run records                                 | `strategy_mode` (`consolidate` / `serial` / empty when not applicable)                                                                                                              |
| run records                                 | `outcome`, e.g. `consolidated 12 PRs into #1801`, parsed case-insensitively from the final `STAFF_RESULT:` line (`consolidated N PRs into #M`); shown in the run log and run detail |

Both run columns are additive (`PRAGMA`-guarded `ALTER TABLE`, like
`cost_method`); older nodes simply omit them.

## Environment

| Variable                     | Default                                                                         | Meaning                                                                            |
| ---------------------------- | ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `STAFF_SCHEDULER_ENABLED`    | `1`                                                                             | `0`/`false` keeps the scheduler thread from starting                               |
| `STAFF_HOLDS_FILE`           | `<config dir>/staff_holds.json`                                                 | Holds list                                                                         |
| `STAFF_SCHEDULE_STATE`       | `<config dir>/staff_schedule_state.json`                                        | Per-role cursor / last fired                                                       |
| `STAFF_ROLES_DIR`            | sibling `Repository_Management/staff/roles`                                     | Role YAML directory                                                                |
| `STAFF_RUNS_DB`              | `<config dir>/staff_runs.sqlite3`                                               | Run store                                                                          |
| `STAFF_REPOS_ROOT`           | `~/Repositories`, `~/actions-runners/repos`, `/mnt/c/Users/<user>/Repositories` | Where checkouts live (`os.pathsep` list)                                           |
| `STAFF_OLLAMA_URL`           | `127.0.0.1:11434` if listening, else the WSL default gateway `:11434`           | Ollama server for the `ollama` and `claude-ollama` providers                       |
| `STAFF_WORKTREES_ROOT`       | `<first repos root>/_staff_worktrees`                                           | Worktree location                                                                  |
| `STAFF_RM_ROOT`              | sibling `Repository_Management`                                                 | Lease ritual scripts                                                               |
| `STAFF_RM_PYTHON`            | `python3` / `python`                                                            | Interpreter for the RM scripts                                                     |
| `STAFF_MAX_CONCURRENT_RUNS`  | `3`                                                                             | Runs executing at once on this node                                                |
| `STAFF_RUN_TIMEOUT_SECONDS`  | `14400`                                                                         | Hard stop per run                                                                  |
| `STAFF_PEER_TIMEOUT_SECONDS` | `6`                                                                             | Per-peer timeout for board fan-out (forwarded dispatches allow 5×)                 |
| `STAFF_BUDGET_USD_PER_DAY`   | `0` (unlimited)                                                                 | Fleet-wide daily ceiling reported by `/api/staff/usage`                            |
| `STAFF_WALL_USD_PER_MIN`     | unset                                                                           | `provider=rate,...` wall-time fallback for providers without token accounting      |
| `CLAUDE_CONFIG_DIR`          | unset (CLI uses `~/.claude`)                                                    | Service-owned Claude seat; required under `ProtectHome=read-only` (see Node setup) |
| `GIT_CONFIG_GLOBAL`          | unset                                                                           | Isolated git config for staff clones and pushes (see Node setup)                   |

## Fleet focus (#1239)

Every staff prompt ends with the role instructions, the target, the PR-consolidation paragraph (when
the role has one), a **Fleet focus** paragraph and the fleet rules. Fleet focus lists at most five items
from `GET /api/priorities`: active items from the latest board meeting whose project names the run's
repo, and operator directives scoped to that repo or `*`. The agent is told to prefer work that advances
them and never to contradict a directive. The paragraph appears as `focus` in a dry-run plan
(`"dry_run": true`). A node without the priorities module or an RM checkout just omits it.

## Node setup (#1223)

Verified machine state, deployment identity, provider health evidence, network
configuration, and rollback paths are recorded in the
[OGLaptop worker runbook](operations/oglaptop-staff-worker.md) and
[ControlTower worker runbook](operations/controltower-staff-worker.md). Check their
verification dates and the live API before assuming the recorded state still holds.

A node that runs staff roles needs four things beyond the dashboard install. DeskComputer was set up this
way on 2026-09-22; `_deploy/node_bootstrap_staff_hub.sh` in the operator workspace does all of it.

1. **systemd drop-in** `/etc/systemd/system/runner-dashboard.service.d/staff-hub.conf`. The provider CLIs are
   Node/V8 (they need W^X memory) and keep state in the home directory, which the unit mounts read-only:

   ```ini
   [Service]
   MemoryDenyWriteExecute=false
   ReadWritePaths=%h/.claude %h/.claude.json %h/.codex %h/.gemini %h/.antigravity %h/.cache %h/.local/share
   ReadWritePaths=%h/staff-repos %h/staff-worktrees %h/.config/gh %h/.cursor %h/.config/cursor
   ```

   Create each path before `daemon-reload`. A missing `ReadWritePaths` entry stops the unit from starting.

2. **Service-owned Claude config**: `CLAUDE_CONFIG_DIR=~/.config/runner-dashboard/claude` in the service env.
   Under `ProtectHome=read-only` the Claude CLI cannot create its `~/.claude.lock` / `~/.claude.json.lock`
   lock directories, so it never refreshes its 8-hour OAuth access token. Every run after expiry then fails
   with `401 OAuth access token has expired`. Inside the writable config directory the lock is
   `~/.config/runner-dashboard/claude.lock`, which the unit can create. Log the seat in once with
   `CLAUDE_CONFIG_DIR=~/.config/runner-dashboard/claude claude auth login`, or copy `~/.claude/.credentials.json`
   into it. A login in the plain shell does not update the service's copy.
3. **Isolated git config**: `GIT_CONFIG_GLOBAL=~/.config/runner-dashboard/staff.gitconfig` with the user identity
   and a credential helper that returns `gh auth token`. Runner hosts' `~/.gitconfig` can collect CI-written
   token rewrites (#1216) that break private clones.
4. **Linux-side clones**: `STAFF_REPOS_ROOT=~/staff-repos` (blobless clones) and
   `STAFF_WORKTREES_ROOT=~/staff-worktrees`. Git inside WSL cannot use worktrees that Windows git created
   under `/mnt/c`.

### Live Repository Management source (#1258)

Use the dedicated **Linux** clone on `main`, with these absolute paths in
`~/.config/runner-dashboard/env` (back up that file before changing it):

```ini
STAFF_RM_ROOT=/home/dieterolson/staff-repos/Repository_Management
STAFF_ROLES_DIR=/home/dieterolson/staff-repos/Repository_Management/staff/roles
GIT_CONFIG_GLOBAL=/home/dieterolson/.config/runner-dashboard/staff.gitconfig
```

Keep the former `~/staff-bundle/rm` directory for rollback; it is no longer the
role source after migration. Preserve each node's scheduler setting: OGLaptop
and ControlTower stay at `STAFF_SCHEDULER_ENABLED=0`; DeskComputer owns scheduling.

Install the packaged `deploy/systemd-user/runner-dashboard-rm-sync.service` and
`.timer` into `~/.config/systemd/user` (back up existing units first), then:

```bash
systemctl --user daemon-reload
systemctl --user enable --now runner-dashboard-rm-sync.timer
systemctl --user start runner-dashboard-rm-sync.service
systemctl --user status runner-dashboard-rm-sync.timer --no-pager
```

This assumes the normal `~/actions-runners/dashboard` deployment. Adjust the
unit paths for a different deployment. Enable user lingering if this user
manager is not kept running (`sudo loginctl enable-linger "$USER"`). The timer
runs independently of the staff scheduler, including on worker nodes. It fetches
at most once per 15 minutes, with a persisted throttle, and only fast-forwards
clean `main`. Dirty, untracked, ahead, diverged or non-main checkouts are skipped
and logged; fetch failures do not interrupt the dashboard. Existing Git
`refs/staff-rm-backups/bak-<date>` preserve every pre-update tracked tree. Never
reset or clean a skipped checkout automatically. Use `systemctl --user start`
for manual checks so the oneshot unit serializes invocations.

The dashboard rereads YAML on each roster/run/scheduler evaluation; it has no
role cache to restart. The timer alternative deliberately keeps Git/network work
outside request and scheduler threads. `/api/staff/roles` aliases `/roster`.
`/api/staff/board?local=1` includes `rm_source` with last checked commit,
commit/check ages in seconds, status, reason and backup ref. `not_checked` means
the timer has not reported this configured clone. A stale check age means the
timer is not refreshing, even if the commit itself happens to be current.

**Holds trap:** the first read seeds a missing holds file from role YAML. Preserve
and back up the existing `staff_holds.json` during migration; do not delete it to
refresh roles. After switching and restarting once to reload the env, inspect
`GET /api/staff/schedule`: worker roles' `hold` fields must be empty. Review and
explicitly lift stale seeded holds through the holds API if present; do not clear
unrelated operator holds. Future role fast-forwards need no dashboard restart.

### Ollama for WSL (#1257)

Keep Windows Ollama's **Expose to network** setting off. Remove non-loopback
`OLLAMA_HOST` values from User/Machine settings as the owner and restart Ollama.
The server must listen on `127.0.0.1:11434`. Do not use mirrored networking.
Leave `STAFF_OLLAMA_URL` unset: the provider discovers the WSL default gateway.

From a checkout containing this change, inspect the plan in PowerShell:

```powershell
powershell.exe -NoProfile -File .\deploy\windows\ollama-wsl-bridge.ps1 -Install -DryRun
```

The **owner**, in an elevated PowerShell, installs it with the same command
without `-DryRun`. OGLaptop's earlier manually configured bridge requires the
explicit `-AdoptExisting` flag on both commands; adoption only accepts its exact
narrow address/subnet/interface rule and loopback forward. Foreign resources
are refused, including conflicting port 11434 forwards and same-name tasks.

The script copies itself into `%ProgramData%\RunnerDashboard\OllamaWslBridge`,
secured for administrators/SYSTEM writes, and registers hidden highest-privilege
SYSTEM task `StaffHub-Ollama-WSL-Bridge`. Startup, logon and five-minute retry
triggers discover an already-running WSL adapter; the task does not start WSL
as SYSTEM. A missing adapter causes a safe wait. Repeated unchanged runs do
not create backups or rewrite rules. An IP change replaces only owned forwards.
Port 8321 and the existing `WSL-PortForward` task are untouched.

The single `StaffHub-Ollama-WSL` firewall rule permits inbound TCP 11434 only
on the discovered WSL interface, local IPv4 address and WSL subnet. Broad inbound
`ollama.exe` rules with remote `Any` are backed up and **disabled**, never deleted.
Portproxy dumps, firewall/filter XML, task XML, prior script/state and JSON result
backups remain in that ProgramData directory. `result.json` records the last
change; Task Scheduler records execution status. `-Uninstall` disables the task
and owned firewall rule and removes only backed-up owned forwards, retaining files.
Rollback is owner-reviewed from those backups; do not restore an entire portproxy
dump blindly over other forwards added since the backup.

After installation, after WSL shutdown/restart, and after a Windows reboot,
verify from a WSL script file:

```bash
gateway=$(ip route | awk '/default/{print $3; exit}')
curl --fail --max-time 10 "http://$gateway:11434/api/version"
```

Then run the `ollama` and `claude-ollama` dashboard ad-hoc health checks and
confirm both reach `succeeded`. From a different tailnet host, port 11434 must
remain unreachable. These are operator acceptance steps; dry-run tests do not
establish reboot or external-connectivity success. OGLaptop remains scheduler-off.

### Node LAN addressing: duplicate-address outages (#1270)

A node that reboots can be handed a DHCP address that another LAN device still
uses. Windows then logs System/Tcpip event **4199** ("duplicate IP address"),
and the node loses DNS, internet and Tailscale although its services recover.
This happened to OGLaptop on 2026-09-23. The other device kept answering ARP,
but not ping, for an address outside the router's lease table. Typical sources
are TVs, cameras and IoT devices with a static address inside the DHCP pool, or
devices that keep an old lease across a router or mesh-node restart. Firewall
settings and the Ollama bridge play no part. Do not disable firewall profiles
to diagnose it.

**Prevent (owner, on the router or mesh app):**

- Give every staff node a DHCP reservation, tied to the MAC of its active LAN
  interface. On eero: _Settings → Network settings → Reservations & port
  forwarding_.
- Find the conflicting device by its MAC in the router's client list. Give it a
  reservation too, or move its static address outside the DHCP pool.
- Turn off random/private hardware addresses on the node's Wi-Fi profile, so the
  reservation matches.
- Keep one active interface per node on the LAN. A wired node with Wi-Fi also
  joined to the same network can keep an expired Wi-Fi lease or a `169.254.x`
  address that confuses route selection.
- Record node MACs and reservations in the private deploy notes, not in this
  public repository.

**Diagnose (read-only), on the node:**

```powershell
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Tcpip'; Id=4199; StartTime=(Get-Date).AddDays(-1)} |
  Format-List TimeCreated, Message
Get-NetNeighbor -AddressFamily IPv4 | Where-Object State -ne 'Unreachable' |
  Group-Object LinkLayerAddress | Where-Object Count -gt 1   # one MAC on many IPs = stale entries or a bridge
```

To compare what two nodes see for the conflicting address, run on each:
`Test-Connection -Count 1 -Quiet <ip>`, then `Get-NetNeighbor -IPAddress <ip>`. A MAC with
no ping reply is a device holding that address.

**Recover:** reconnect the adapter, or run `ipconfig /release` then `ipconfig /renew`,
to get a different lease. Then fix the router reservations so the next reboot
does not repeat it.

## Three-node fleet acceptance (#1273)

The Staff Hub operates across three primary hardware nodes:
1. **DeskComputer** — The dedicated fleet scheduler (`STAFF_SCHEDULER_ENABLED=1`).
2. **OGLaptop** — Worker node (`STAFF_SCHEDULER_ENABLED=0`). Runbook: [OGLaptop worker runbook](operations/oglaptop-staff-worker.md).
3. **ControlTower** — Worker node (`STAFF_SCHEDULER_ENABLED=0`). Runbook: [ControlTower worker runbook](operations/controltower-staff-worker.md).

### Unified Acceptance Standard

All nodes must pass the single unified acceptance test:

```bash
deploy/staff-node-acceptance.sh [--role worker|scheduler] [--run-ad-hoc]
```

### Fleet Qualification Matrix

| Dimension | DeskComputer | OGLaptop | ControlTower |
| --- | --- | --- | --- |
| **Role** | Scheduler | Worker | Worker |
| **`STAFF_SCHEDULER_ENABLED`** | `1` | `0` | `0` |
| **Deployment** | 4.10.0+ on loopback `:8321` | 4.10.0+ on loopback `:8321` | 4.10.0+ on loopback `:8321` |
| **Live RM Source** | `~/staff-repos/Repository_Management` | `~/staff-repos/Repository_Management` | `~/staff-repos/Repository_Management` |
| **Sync Timer** | `runner-dashboard-rm-sync.timer` | `runner-dashboard-rm-sync.timer` | `runner-dashboard-rm-sync.timer` |
| **User Lingering** | Enabled (`Linger=yes`) | Enabled (`Linger=yes`) | Enabled (`Linger=yes`) |
| **Git Config** | `staff.gitconfig` (gh auth helper) | `staff.gitconfig` (gh auth helper) | `staff.gitconfig` (gh auth helper) |
| **Windows Ollama** | Bound `127.0.0.1:11434` | Bound `127.0.0.1:11434` | Bound `127.0.0.1:11434` |
| **Ollama WSL Bridge** | `StaffHub-Ollama-WSL-Bridge` SYSTEM task | `StaffHub-Ollama-WSL-Bridge` SYSTEM task | `StaffHub-Ollama-WSL-Bridge` SYSTEM task |
| **Provider Suite** | Claude, Codex, Antigravity, Cursor, Ollama | Claude, Codex, Antigravity, Cursor, Ollama | Claude, Codex, Antigravity, Cursor, Ollama |
| **Worker Holds** | N/A (manages holds) | Zero blocking worker holds | Zero blocking worker holds |

### Acceptance Criteria Checklist
1. **Deployment & API**: `curl -fsS http://127.0.0.1:8321/api/health` returns status `ok`.
2. **Identity**: `gh auth status` confirms valid WSL GitHub authentication with no embedded tokens.
3. **CLIs**: Node v24 LTS in PATH; `claude`, `codex`, `agy`, `cursor-agent` executable.
4. **Service Drop-in**: `staff-hub.conf` has `MemoryDenyWriteExecute=false`, provider PATH, and all required `ReadWritePaths`.
5. **Dynamic Role Sync**: `rm_source` in `/api/staff/board?local=1` reports status `ok`; active timer refreshes at most every 15 min.
6. **Holds**: `/api/staff/schedule` reports no worker roles blocked by active holds.
7. **Scheduler Invariant**: Only DeskComputer runs with `STAFF_SCHEDULER_ENABLED=1`; worker nodes strictly enforce `0`.
8. **Ollama Reachability**: Gateway `11434/api/version` returns HTTP 200 without exposing Ollama to the physical LAN or Tailscale.
9. **Provider Verification**: Ad-hoc health check runs succeed with exit code 0 and emit `STAFF_RESULT: ok`.

## Provider Options (#1252)

| Provider        | Launch                                                                                        | Models                                          |
| --------------- | --------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| `claude`        | `claude -p --output-format stream-json --permission-mode bypassPermissions`                   | Claude seat (`CLAUDE_CONFIG_DIR` service copy)  |
| `codex`         | `codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check` (0.156+)        | ChatGPT seat                                    |
| `antigravity`   | `agy --print --output-format stream-json --dangerously-skip-permissions`                      | Google sign-in (replaces the older Gemini CLI)  |
| `cursor-agent`  | `cursor-agent -p --output-format stream-json --force --trust --workspace <wt>`                | Cursor subscription, incl. Grok (`grok-4.7-*`)  |
| `ollama`        | `codex exec --oss --local-provider ollama` with `CODEX_OSS_BASE_URL=<ollama>/v1`              | Any Ollama model; default `glm-5.3-flash:cloud` |
| `claude-ollama` | Claude Code with `ANTHROPIC_BASE_URL=<ollama>`, own `CLAUDE_CONFIG_DIR` (`.../claude-ollama`) | Any Ollama model; default `glm-5.3-flash:cloud` |
| `gemini`        | `gemini -p` (legacy; prefer `antigravity`)                                                    | Google                                          |

Ollama models always run inside an agent harness (Codex or Claude Code), so they can edit, commit and
open PRs; bare `ollama run` chat cannot. On a NAT-mode WSL node the server is the Windows Ollama app,
reached through the default gateway. Pass `model` on a run to pick another Ollama model
(`kimi-k3:cloud`, `deepseek-v4-pro:cloud`, …) or another Cursor model (`cursor-grok-4.6-high`, …).

Check a node with a short ad-hoc run (`POST /api/staff/ad-hoc/run` with `{"provider": "claude", "prompt": "..."}`)
and read `GET /api/staff/schedule`. Its `hold` column must be empty for scheduled worker roles.

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
