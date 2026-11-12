# 0011. Pool controller: lane files, claim-as-dequeue, and drain semantics

## Status

- Accepted (slice 1 implemented behind `DASHBOARD_ENABLE_POOL_CONTROLLER`, default off)

## Context

WP27 (#4279) tracks the adversarial-review finding cluster K-13 / C-07..C-15: the fleet has job-board claims, backlog-push lanes, and coordination APIs, but no unified **pool controller** that (a) treats issue claims as queue dequeue, (b) scales lane count from demand, and (c) drains or refills safely. Today backlog-push records lane ownership in ad-hoc JSON (`*.receipt.json`, `partial_active.json`) only **after** spawn (C-10 launch-then-record), and lifecycle strings are implicit across receipts, labels, and sidecars (C-08).

Wave 4 gates full pool UI and controller activation on WP12–WP15 and review-flow metrics (#4250). Slice 1 still needs a written contract and a safe, default-off foothold in the launcher path.

## Decision

1. **Lane file schema** (`pool-controller-lane.v1`): per-session file `{session}.lane.json` beside backlog-push logs, fields `schema_version`, `session`, `role`, `state`, `pid`, `updated_at`. Implemented in `backend/pool_controller/lane_file.py`.
2. **`LaneState` enum** with an explicit transition table matching today's receipt status literals (`active`, `recovered`, `rolled_back`, `termination_pending`) plus pre-spawn `pending` (C-08 / char tests in `tests/unit/test_pool_controller_lane_state.py`).
3. **Write-before-spawn (C-10):** when `DASHBOARD_ENABLE_POOL_CONTROLLER=1`, `backlog-push-spawn.py` writes `pending` before `_spawn_one`, then transitions to `active` (or `recovered` when adopting a pid) before updating the legacy receipt.
4. **Claim-as-dequeue (deferred wiring):** the controller loop will call `POST /api/coordination/jobs/claim` under a fence, bind the returned issue onto the lane file, and release on terminal lane states. No controller process ships in slice 1; API contract is unchanged.
5. **Controller loop (deferred):** periodic reconcile: read lane files → compare caps from config (not hardcoded) → reap dead lanes → refill from job board while not draining. Drain sets a hub-local flag; no new spawns, existing lanes finish or timeout.
6. **Feature flag:** `DASHBOARD_ENABLE_POOL_CONTROLLER` (default `false`) gates all lane-file side effects; legacy behavior is unchanged when off.

## Consequences

**Easier**

- Launchers and future controller share one durable lane record instead of inferring lifecycle from receipts alone.
- Characterization tests lock receipt literals while allowing typed transitions.
- Default-off rollout fits Wave 4 gating without risking barb-worker pushes.

**Harder / follow-ups**

- Controller must respect single-flight backlog push, hub pressure gates, and lease fencing before claim-as-dequeue goes live.
- G2-09 operator UI for agent count and provider mix remains a later slice.
- Caps move from literals to config when the controller loop lands (K-19).

Refs #4279, parent epic #4258.
