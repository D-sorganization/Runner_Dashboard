# Fleet restic rollout (#4012)

Repo-only rollout artifacts for **Desk**, **ControlTower**, and **Brick** after the
OGLaptop restic pilot (issue #4011). This change does **not** SSH to hosts or run
`restic` on fleet machines.

## Configuration

- `config/fleet_restic_hosts.json` — host ids, env var names, backup path list
- Pilot gate: set `FLEET_RESTIC_PILOT_COMPLETE=1` only after #4011 succeeds

## Operator CLI

```bash
python3 deploy/restic_rollout_plan.py              # dry-run JSON report
FLEET_RESTIC_PILOT_COMPLETE=1 python3 deploy/restic_rollout_plan.py --apply
```

Apply mode records install steps via the planner hook; production install still
requires owner-run systemd enable on each node (see `deploy/install-node-self-heal.sh`
restic timer block when added post-pilot).

## Systemd

`deploy/systemd/runner-dashboard-restic-rollout.{service,timer}` default to
`FLEET_RESTIC_ROLLOUT_APPLY=0` (log-only). Do not set apply env until Dieter
confirms pilot + restore drills (#4013).
