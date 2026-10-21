# Node-local auto-deploy (pull-based)

Runner Dashboard fleet nodes and the hub can apply new `main` releases without
manual `update-deployed.sh` runs. A **systemd timer** (default every 10 minutes)
invokes `deploy/node-auto-deploy.sh`, which:

1. Reads `origin/main` and requires **green check runs** on that commit (GitHub
   API via the node’s existing `GH_TOKEN` in `~/.config/runner-dashboard/env`).
2. Downloads the matching **`dashboard-X.Y.Z.tar.gz`** release asset.
3. Installs into `~/actions-runners/dashboard-releases/<sha>/` and switches the
   live deploy dir via symlink when migrated.
4. Restarts **system** or **user** `runner-dashboard` (Brick user units never
   install a duplicate system service).
5. Runs `/api/health` and `GET /api/staff/summary` (with `HUB_FLEET_TOKEN` when
   set). On failure, rolls back to the previous release and records the event.

**Hub-first:** nodes with `HUB_URL` wait until the hub’s `/api/fleet/status`
shows the same `deployment.git_sha` at the target commit.

**Staff safety:** deploy is skipped while `in_flight` staff runs exist; the timer
retries until the drain timeout (default 15 minutes).

## One-time install (each node)

From a checkout that includes this runbook (or after the stack merges to `main`):

```bash
bash deploy/install-node-auto-deploy.sh
```

This copies the auto-deploy scripts into the live deploy dir, installs the timer
(system scope on DeskComputer WSL hub, user scope on Brick), and enables it.
The generated unit pins `RUNNER_DASHBOARD_PYTHON` to the same interpreter as
`runner-dashboard` (deploy `.venv` or the dashboard unit’s `ExecStart`).

After upgrading auto-deploy on a live hub, pull `main` and re-run the installer
so systemd picks up the pinned interpreter:

```bash
bash deploy/install-node-auto-deploy.sh
sudo systemctl start runner-dashboard-auto-deploy.service
```

### Legacy flat deploy directory

If `~/actions-runners/dashboard` is still a plain directory, the first successful
auto-deploy renames it to `dashboard-releases/legacy-pre-auto-deploy` and replaces
the path with a symlink to the new release.

## Kill switch

- Environment: `RUNNER_DASHBOARD_AUTO_DEPLOY=0` in `~/.config/runner-dashboard/env`
- Flag file: `~/.config/runner-dashboard/auto-deploy.disabled`

## Observability

- State file: `~/.config/runner-dashboard/auto_deploy_state.json`
- Fleet UI / Barb: `GET /api/fleet/status` → `auto_deploy` on each node
  (`attention: true` after rollback or hard failure).

## Manual dry-run

```bash
bash ~/actions-runners/dashboard/deploy/node-auto-deploy.sh --dry-run
```
