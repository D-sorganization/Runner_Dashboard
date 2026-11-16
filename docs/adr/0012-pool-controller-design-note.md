# 0012. Pool controller design note: lane file v2, claim-as-dequeue, reconcile loop and drain

## Status

- Proposed. Design only: no controller code ships with this note.
- Extends [0011](./0011-pool-controller-lane-files-and-claim-queue.md), which recorded slice 1 (#4370) and left the claim wiring, the loop and drain as "deferred".
- The build gate in #4279 is **not met** on 2026-11-14 (see [Gate evidence](#gate-evidence)).

## Context

WP27 (#4279, epic #4258) groups these findings of the 2026-11-10 adversarial review. The wording is the one in #4279; the full review text is not in this repository.

| Finding          | Problem                                                                           | Answered in                                 |
| ---------------- | --------------------------------------------------------------------------------- | ------------------------------------------- |
| K-13             | The issue queue is never consumed or locked, and nothing scales lanes up or down. | Claim-as-dequeue, Controller loop           |
| C-07             | The relay can launch Claude lanes only.                                           | Lane file v2 (`provider`, `model`)          |
| C-08, K-16       | Push and lane lifecycle is implicit across JSON files, labels and sidecars.       | Lane states                                 |
| C-09             | A lane that passes its deadline is orphaned.                                      | Controller loop, step 3                     |
| C-10             | The launcher spawned first and recorded afterwards.                               | Done in slice 1; kept in Claim-as-dequeue   |
| C-11             | The relay trusted stale pid files.                                                | Fixed by #4263; the loop reuses that check  |
| C-12             | A dead lane is never restarted.                                                   | Controller loop, steps 3 and 6              |
| C-15             | The lane count is fixed and a push cannot be cancelled.                           | Controller loop, Drain                      |
| K-18             | Shared files are written without a lock.                                          | Lane file v2 (write rule)                   |
| K-19             | Caps are hardcoded.                                                               | Configuration                               |
| G2-09            | No UI or API for agent count, provider mix, skill or lanes.                       | Deferred; the loop writes the data it needs |
| C-13, C-14, K-17 | In the cited range, but #4279 gives no text for them.                             | **Not addressed here**                      |

What exists on main today:

- `backend/pool_controller/` (slice 1): lane file `pool-controller-lane.v1` and `LaneState` with a transition table. `scripts/barb-worker/backlog-push-spawn.py` writes the file before it spawns, only when `DASHBOARD_ENABLE_POOL_CONTROLLER=1` (default off).
- The job board: `GET /api/coordination/jobs` lists claimable issues. `POST /api/coordination/jobs/claim` takes one atomically and returns a fence. `POST /jobs/heartbeat` and `POST /jobs/release` need that fence. The lease lasts 2 hours by default.
- The launcher takes a fixed plan on stdin and starts every lane in it once. Nothing looks at the lanes again.

### Gate evidence

#4279 allows the build "only after WP12-WP15 land and the review-flow metrics (#4250) show demand".

**Work packages.** None of the four has landed in full (state on 2026-11-14):

| Package | Issue | State                                                          |
| ------- | ----- | -------------------------------------------------------------- |
| WP12    | #4266 | open                                                           |
| WP13    | #4267 | open                                                           |
| WP14    | #4269 | open                                                           |
| WP15    | #4270 | open; durable fences (B-09) are in review as #4446, not merged |

WP15 matters most here: until fences survive a hub restart, a controller that holds claims for its lanes loses all of them on every restart.

**Demand metrics.** #4250 is closed and `GET /api/v1/staff/review/metrics` works. One reading from the hub at 2026-11-14 21:30 UTC:

| Metric                                         | Value                                              |
| ---------------------------------------------- | -------------------------------------------------- |
| Queue depth (green, non-draft, unapproved PRs) | 38 (UD 13, RD 10, RM 6, AD 4, GM 4, Tools 1)       |
| Verdict mix                                    | approve 0, changes 0, escalate 0                   |
| Review runs by machine and provider            | none recorded                                      |
| Review latency p50/p90                         | deferred by the endpoint: needs the #4249 contract |
| Reviewer disagreement rate                     | deferred by the endpoint: needs the #4249 contract |
| Dispatch 429s                                  | 0, counted since the last process start            |

This does not show demand for a pool controller, for three reasons:

1. **There is no history.** The endpoint computes a snapshot on each request and stores nothing. The gate in #4251 asks for 7 days of data, and #4251 records no decision.
2. **Two of the seven metrics are not implemented**, and two more are empty or reset on restart.
3. **It measures the wrong queue.** These are review-flow numbers. The pool controller scales implementation lanes, whose demand is the number of claimable jobs compared with the number of lanes working. Nothing measures that today.

So the evidence for the gate is missing. Under [What would open the gate](#what-would-open-the-gate) this note lists the numbers that would settle it.

## Decision

The rules below are what the controller must do when it is built. Until the gate opens, nothing in this note is implemented.

### 1. Lane file v2

One file per lane, `{session}.lane.json`, in the push log directory. v2 only adds fields to v1, and a v2 reader accepts a v1 file by reading the new fields as `null`.

| Field                             | Type           | Meaning                                                                       |
| --------------------------------- | -------------- | ----------------------------------------------------------------------------- |
| `schema_version`                  | string         | `pool-controller-lane.v2`                                                     |
| `session`, `role`, `state`, `pid` | as in v1       | unchanged                                                                     |
| `provider`, `model`               | string or null | which CLI and model the lane runs (C-07)                                      |
| `host`                            | string         | machine that owns the process; a controller acts only on its own host's lanes |
| `pid_started_at`                  | number or null | process start time, so that a reused pid is never taken for the lane (C-11)   |
| `push_id`                         | string         | the push this lane belongs to (C-08)                                          |
| `claim`                           | object or null | `{repo, issue, fence, expires_at}` of the job the lane holds                  |
| `deadline_at`                     | number or null | when the lane must have stopped (C-09)                                        |
| `state_reason`                    | string         | one line: why the lane is in this state                                       |
| `created_at`, `updated_at`        | number         | Unix seconds                                                                  |

**Write rule (K-18).** Every writer takes an exclusive `flock` on `pool.lock` in the same directory, writes a temporary file, calls `fsync`, and renames it over the lane file. v1 writes in place with `write_text`, so a crash can leave half a file; the first slice after the gate replaces that.

**Read rule.** A lane file that cannot be read counts as an occupied lane and is reported. The controller never deletes or rewrites a file it cannot parse.

### 2. Lane states

v1's five states stay, with their values. Two states are added, and `termination_pending` stops being terminal: today a lane that was sent `SIGTERM` stays in that state for ever, and nothing confirms that it stopped (C-09).

| State                 | Meaning                                          | May go to                                                   |
| --------------------- | ------------------------------------------------ | ----------------------------------------------------------- |
| `pending`             | file written, job claimed, process not started   | `active`, `recovered`, `rolled_back`                        |
| `active`              | process running                                  | `draining`, `termination_pending`, `exited`, `rolled_back`  |
| `recovered`           | a running process was adopted after a restart    | same as `active`                                            |
| `draining` (new)      | running, but finishes its job and gets no other  | `active` (drain cancelled), `termination_pending`, `exited` |
| `termination_pending` | `SIGTERM` sent; waiting for the process to go    | `exited`                                                    |
| `rolled_back`         | terminal: the launch failed and was undone       | none                                                        |
| `exited` (new)        | terminal: process confirmed gone, claim released | none                                                        |

A lane is never restarted in place. When it ends, its job goes back to the board and a new lane with a new session takes the next job (C-12).

### 3. Claim-as-dequeue

The job board is the queue, and a successful claim is the dequeue. There is no second queue (K-13).

The controller, not the lane, claims the job. This keeps the record-before-spawn order of slice 1 and works for every provider (C-07, C-10):

1. Write the lane file as `pending`.
2. `POST /jobs/claim` with the lane's session and agent. A 409 means another agent holds the job: try the next job, at most three per tick.
3. Write `claim` (with the fence) into the lane file.
4. Spawn the lane and set `active`.
5. If the spawn fails: `POST /jobs/release` with the fence, then `rolled_back`.

While the lane runs, the controller sends `POST /jobs/heartbeat` for it on every tick. The lane does not need to know the fence.

When a lane reaches `exited` or `rolled_back`, the controller releases the claim with the fence. If the lane opened a PR, the job board already excludes the issue; if not, the issue is claimable again.

Failure cases:

| Case                                               | Result                                                                                           |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Crash after step 2, before step 3                  | The claim has no lane file. It expires with its lease, and the job-board sweeper releases it.    |
| Heartbeat answers "stale fence"                    | The lane has lost the job. The controller sends `SIGTERM` and sets `termination_pending`.        |
| Hub restart                                        | Claims and fences must survive it. This needs WP15 (B-09, #4446) and is one reason for the gate. |
| The agent is blocked by its own aging PRs          | The claim is refused. The controller starts no lane for that agent on this tick.                 |
| The same issue ends two lanes without a PR in 24 h | The controller skips that issue and reports it. This stops a crash loop on one bad job.          |

A known limit: a fence stops a stale holder at the job board, not at GitHub. A lane that has lost its claim can still push until its process ends, which is why the controller terminates it at once.

### 4. Controller loop

One controller runs per host. It keeps no state in memory: on every tick it derives everything from the lane files, the process table, the job board and the configuration. A second controller that cannot take `pool.lock` exits.

Each tick (60 s by default):

1. **Read** the configuration and every lane file of this host.
2. **Observe** each lane that is not terminal. Is the process alive, and is it this lane's worker? The check is the one #4263 added to the launcher (`_verify_process_identity`), plus `pid_started_at`.
3. **Reap.** A lane whose process is gone becomes `exited`. A lane past `deadline_at` gets `SIGTERM`, but only after the identity check, and becomes `termination_pending`; after the grace period it gets `SIGKILL`. Claims of `exited` lanes are released.
4. **Heartbeat** the claims of live lanes.
5. **Count.** `wanted = min(max_lanes, claimable jobs + live lanes)`, then apply the cap of each provider and the hub pressure gates. While draining, `wanted` is the number of live lanes.
6. **Refill.** If fewer lanes are live than wanted, start at most `max_spawns_per_tick` new lanes by the five steps of section 3.
7. **Report.** Write `pool_status.json`: lanes by state, claims held, jobs claimable, lanes wanted, and each action taken or skipped with its reason. The later API and UI read this file (G2-09).

Scaling down never kills a working lane. The controller only stops refilling, and the count falls as lanes finish (C-15).

A dry-run mode does steps 1, 2, 5 and 7 only. It spawns nothing, signals nothing and claims nothing, and it records what it would have done.

### 5. Drain

Drain means: start nothing new, and let running lanes finish.

- **Start.** The file `pool.drain` in the push log directory holds `{requested_by, reason, deadline_at}`. Because it is a file, a drain survives a controller restart.
- **While draining.** No claim and no spawn. Live lanes keep their heartbeats and move to `draining`. A lane that finishes is reaped as usual.
- **Deadline.** At the drain's `deadline_at`, or the lane's own if that is earlier, the remaining lanes get `SIGTERM`, then `SIGKILL` after the grace period.
- **Done.** The pool is drained when every lane is terminal and every claim is released. The controller writes this into `pool_status.json`.
- **Cancel.** Removing `pool.drain` before the deadline returns `draining` lanes to `active`, and refilling resumes.
- **Cancel a push (C-15).** This is a drain whose deadline is now.

A hub restart or deploy is not a drain. Lanes are detached processes and keep running; the next controller adopts them as `recovered`, as the launcher does today.

### 6. Configuration (K-19)

One file, read on every tick, checked against a schema. A file that fails the check stops refilling and is reported; running lanes are not touched. The defaults equal today's fixed values, so turning the controller on changes nothing until an operator edits the file.

| Key                       | Meaning                                |
| ------------------------- | -------------------------------------- |
| `max_lanes`               | upper limit of live lanes on this host |
| `provider_caps`           | upper limit per provider               |
| `max_spawns_per_tick`     | default 1                              |
| `tick_seconds`            | default 60                             |
| `lane_deadline_minutes`   | sets `deadline_at` when a lane starts  |
| `terminate_grace_seconds` | wait between `SIGTERM` and `SIGKILL`   |
| `repos`, `tiers`          | which jobs this pool may claim         |
| `dry_run`                 | default true                           |

`DASHBOARD_ENABLE_POOL_CONTROLLER` stays the master switch and stays off by default.

### What would open the gate

These numbers do not exist today. The board decides the threshold; this note only names what to measure:

- claimable jobs on the board over time, per repo and tier;
- live lanes over time, and how long a lane slot stays empty while claimable jobs exist;
- how often a lane dies or passes its deadline and nobody notices (C-09, C-12);
- how often a person has to change the lane count by hand (C-15).

The dry-run mode of section 4 would produce the first three without acting on anything. It is still controller code, so it also waits for a decision.

### Order of slices after the gate

1. Lane file v2 with the locked, atomic write, and the two new states. Characterization tests first.
2. Controller in dry-run mode: read, observe, count, report.
3. Reap, release and heartbeat.
4. Refill through claim-as-dequeue.
5. Drain and push cancel.
6. API and UI (G2-09).

## Consequences

**Easier**

- One record per lane says what it runs, which job it holds and when it must stop.
- A dead or overdue lane is found within one tick, and its job goes back to the board.
- Lane count and provider mix become configuration, and a push can be stopped.
- The controller can crash at any point: the next tick starts from the files.

**Harder**

- The controller depends on WP15. Without durable fences every hub restart strands its claims.
- Claims held by the controller make the hub a single point for lane work on that host. If the hub is down longer than the lease, the jobs are released while the lanes still run.
- `termination_pending` changes from terminal to non-terminal. Code that treats it as final must be found and changed in slice 1.
- The lane file becomes a contract between the launcher, the controller and later the UI, so every change needs a version and a reader for the old one.

**Alternatives rejected**

- _A separate queue table._ It would copy the job board and need its own locking.
- _The lane claims its own job._ This is launch-then-record again (C-10), and every provider's CLI would have to implement the claim.
- _Lane state in the hub database._ The launcher runs outside the backend process and must record a lane before the hub knows about it. 0011 chose files for that reason.
- _Kill lanes to scale down._ It throws away work in progress for no gain; lanes are short.

Refs #4279, #4250, #4251, #4270, parent epic #4258.
