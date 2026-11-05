# #3821 — Scheduler pause TTL and expiry alerts

## Problem

Hub scheduler disabled via env without expiry can stall staff runs indefinitely without operator notice.

## Acceptance

1. **TTL** — When `STAFF_SCHEDULER_ENABLED=0`, require `STAFF_SCHEDULER_PAUSE_UNTIL` or apply a documented default TTL; snapshot exposes pause reason + expiry.
2. **Alert** — After expiry, emit fleet event + rd-barb attention (once per pause episode, debounced).
3. **Worker nodes** — Permanent disable on workers unchanged; hub-only scope.
4. **Tests** — Unit tests: no alert before expiry; alert after; TTL auto-set when missing.

## Open PR

- [#3830](https://github.com/D-sorganization/Runner_Dashboard/pull/3830)
