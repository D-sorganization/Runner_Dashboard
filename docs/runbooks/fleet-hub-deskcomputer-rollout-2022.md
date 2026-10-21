# Fleet hub rollout — DeskComputer hub (#2022)

Apply after merging the PR that closes **#2022**. Hub topology and Codex defaults
live in git (`backend/machine_registry.yml`, `config/staff_provider_defaults.json`);
per-host env is override-only.

## Before you restart any dashboard

**DeskComputer interim state (2026-10-20):** operator edits may set
`HUB_URL=http://deskcomputer.tail2bbcc7.ts.net:8321` and an explicit
`FLEET_NODES=...` in `~/.config/runner-dashboard/env`. An explicit `FLEET_NODES`
**disables** registry autoderive. Either remove those lines (restore from
`*.bak-20261020-211149` if needed) or reconcile so they match the registry
before restarting.

## Per node

### DeskComputer (hub)

1. Pull/deploy this merge to `main`.
2. Remove interim `HUB_URL` / `FLEET_NODES` overrides unless you intentionally
   keep a custom peer list (not recommended).
3. Reinstall the unit so any legacy `MACHINE_ROLE=node` line is removed from
   `/etc/systemd/system/runner-dashboard.service` (role comes from the registry
   at runtime):

   ```bash
   cd ~/actions-runners/dashboard
   ./deploy/setup.sh --runners 8 --machine-name DeskComputer
   ```

4. Confirm the unit has **no** `Environment=MACHINE_ROLE=` line unless you
   passed `--role` intentionally; restart if you only edited env files.
5. Confirm `GET /api/fleet/status` on DeskComputer shows `_role: hub` and staff
   summary names DeskComputer as hub.

### ControlTower (node)

1. Deploy merge.
2. Remove `MACHINE_ROLE=hub` from env if still set.
3. Remove `HUB_URL` pointing at ControlTower; spokes should derive hub URL from
   registry (DeskComputer).
4. Restart dashboard inside ControlTower-Runner WSL / host unit.
5. Confirm `_role: node` and federation reaches DeskComputer.

### OGLaptop (node)

1. Deploy merge.
2. Clear custom `HUB_URL` unless debugging; let registry set hub to DeskComputer.
3. Restart dashboard.
4. Spot-check `/api/fleet/nodes` includes Brick after Brick is redeployed.

### Brick (node)

1. Follow [brick-dashboard-redeploy.md](./brick-dashboard-redeploy.md).
2. Ensure registry entry is present (post-merge `machine_registry.yml`).
3. Reinstall the unit (drops stale hub role, derives `HUB_URL` at runtime):

   ```bash
   cd ~/actions-runners/dashboard
   ./deploy/setup.sh --runners 4 --machine-name Brick
   ```

4. Dry-run dispatch to `machine: Brick` from the hub.

## Codex default model

After deploy, staff Codex runs use `config/staff_provider_defaults.json`
(`gpt-6-luna`) via explicit `--model` on `codex exec`. Host `~/.codex/config.toml`
model lines are no longer authoritative for dashboard dispatches. Per-run
`model` in the API still overrides.
