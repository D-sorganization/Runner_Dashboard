# rd-prompt — fleet efficiency (#4562)

Lane and headless CLI sessions use these hub-backed rules:

## Wake coalescing

Finished-lane and CI wake signals are batched per recipient for `FLEET_WAKE_COALESCE_SEC` (default **300**, clamped **300–600**). Coordinators should not expect an immediate wake for every single lane completion inside that window.

Read the effective window from `GET /api/coordination/fleet-efficiency`.

## 6-line completion brief

Inter-agent and lane completion posts that start with `Status:` must follow:

```text
Status: <state>
Result: <one line>
Evidence: <short pointer>
Evidence path: <URL or file path for full logs/diffs>
```

The body must stay at or below **6 lines**. Full logs belong in the evidence path, not the message.

## Planned reset thresholds

Checkout and hub session records include `fleet_efficiency.planned_reset`:

- **≥25** agent turns in one lane session
- **≥150k** tokens on the last turn
- **post-milestone** completion (after a shipped artifact)

When `evaluate_planned_reset` recommends reset, sync RD turnover/inventory before starting a fresh context.