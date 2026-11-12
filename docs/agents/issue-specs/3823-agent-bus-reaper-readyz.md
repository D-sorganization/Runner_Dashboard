# #3823 — Defer cross-host claim reaping until readyz

## Problem

`agent_bus` reaper can release GitHub claims for sessions on a remote node still booting.

## Acceptance

1. **Local principals** — Reap only when local `/readyz` aggregate is ok.
2. **Remote principals** — Resolve node from fleet registry; defer reap until peer `/readyz` ok; unknown node → fail closed (no release).
3. **Tests** — Mock health gate: unhealthy remote retains session; healthy allows reap.

## Open PR

- [#3833](https://github.com/D-sorganization/Runner_Dashboard/pull/3833)
