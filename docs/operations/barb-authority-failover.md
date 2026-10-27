# Barb authority failover runbook

Issue: [#1800](https://github.com/D-sorganization/Runner_Dashboard/issues/1800) (BR-06). Design:
[ADR 0008](../adr/0008-barb-single-authority.md). Script: `scripts/barb_failover.py`.

## Objectives (owner ruling, Repository_Management#1853)

- **RPO = 0** against the authority's durable commit: an acknowledged write is committed with
  `synchronous=FULL`. Losing the authority's disk is not covered until a synchronous standby exists
  (a recorded follow-up).
- **RTO = 15 minutes** for this manual procedure.
- **No split writes.** Exactly one node, the authority, commits conversation state. Replicas stage
  run-card updates in their bounded outbox and deliver them when the authority is back.
- **Read-only, degraded mode during an outage.** Replicas keep no read cache of the authority's
  threads. Workers keep running and their results wait in the outbox.

## Normal topology

| Setting                | Every node                             | Notes                                          |
| ---------------------- | -------------------------------------- | ---------------------------------------------- |
| `BARB_AUTHORITY_NODE`  | `node-a` (the registry `hub`)    | The node that commits threads.                 |
| `BARB_AUTHORITY_URL`   | `http://node-a:8321`             | The authority's stable private URL.            |
| `BARB_AUTHORITY_EPOCH` | current epoch (starts at `1`)          | Bumped on every failover.                      |
| `FLEET_API_URL`        | same as `BARB_AUTHORITY_URL`, or unset | Fleet clients default to `BARB_AUTHORITY_URL`. |

Every enrolled computer reads the same threads because clients talk to the authority URL.
`GET /api/v1/staff/barb/status` on any node shows `role`, `epoch`, `outbox` depth, `fence`,
`fenced` and `degraded`.

## The epoch fence

Each conversation database stores a fence: the highest `(epoch, node)` it has seen (migration 7).

- An authority whose configured epoch is **below** the fence refuses every write
  (`409 stale_authority_epoch`). Its own run cards are staged in its outbox, not lost.
- Naming a **different node at the same epoch** is refused. A node change always bumps the epoch.
- The fence only moves forward. It is advanced by the authority's own writes and by
  `barb_failover.py advance`. It is never learned from a request.

The conversation database is `~/.config/runner-dashboard/staff_runs.sqlite3` unless `STAFF_RUNS_DB`
or `RUNNER_DASHBOARD_CONFIG_DIR` overrides it. Run the script commands below with the dashboard's
Python environment from the repository root.

## Decide first

Fail over only if the authority will not be back within the RTO. A short outage needs no action:
replicas queue their updates and deliver them, deduplicated, when the authority returns. Check:

```bash
curl -s http://node-a:8321/api/v1/staff/barb/status -H "Authorization: Bearer $TOKEN"
```

## Procedure (example: node-a to hub-a, epoch 1 to 2)

Run the steps in order. Each script command prints one JSON document. Exit code 1 means the fence
refused the step: stop and check the epoch and node.

1. **Stop the old authority if you can reach it.** Stop the dashboard service on node-a so it
   cannot accept writes.
2. **Get the newest copy of the authority database.** If node-a's disk is readable, take an
   online backup there. The backup keeps WAL-resident rows, IDs and receipts.

   ```bash
   python scripts/barb_failover.py backup --db ~/.config/runner-dashboard/staff_runs.sqlite3
   ```

   Copy the single backup file to the new authority and put it in place of its conversation
   database (keep the replaced file as a rollback copy). Use the most
   recent backup if the disk is lost. Writes after that backup are the RPO gap that a synchronous
   standby would close.

3. **Promote the new authority's copy.** With the hub-a dashboard stopped and the
   backup in place, run this on hub-a:

   ```bash
   python scripts/barb_failover.py advance --db ~/.config/runner-dashboard/staff_runs.sqlite3 \
     --epoch 2 --node hub-a --reason "node-a down"
   ```

4. **Print the new settings** and apply them to every node, including the new authority:

   ```bash
   python scripts/barb_failover.py env --node hub-a --url http://hub-a:8321 --epoch 2
   ```

   Put the values in each node's `~/.config/runner-dashboard/env`. Restart every dashboard.

5. **Verify.** On hub-a, `/barb/status` shows `role: authority`, `epoch: 2`,
   `fenced: false`. On each replica, it shows `role: replica`, `epoch: 2`, and the outbox drains to 0.
   A replica still on epoch 1 gets `409 stale_authority_epoch` and keeps its updates queued until it
   is reconfigured.
6. **Fence the old authority before it rejoins.** When node-a comes back, keep its dashboard
   stopped and run:

   ```bash
   python scripts/barb_failover.py advance --db ~/.config/runner-dashboard/staff_runs.sqlite3 \
     --epoch 2 --node hub-a --reason "demoted"
   ```

   Then give it the epoch 2 settings from step 4 and start it as a replica. Even with stale
   settings, it now refuses writes and `/barb/status` reports `fenced: true`.

## Failing back

Failing back is another failover with the next epoch (3). Repeat the procedure in the other
direction. Never reuse an old epoch: the fence refuses it.

## What this procedure does not cover

- Automatic failover and a synchronous standby (follow-up; the owner ruling keeps failover manual).
- A degraded banner in the Console and a replica write proxy for user-originated thread writes.
  Both need a UI decision and are tracked as follow-ups to #1800.
