# Node-local auto-deploy (pull-based)

Runner Dashboard fleet nodes and the hub can apply new `main` releases without
manual `update-deployed.sh` runs. A **systemd timer** (default every 10 minutes)
invokes `deploy/node-auto-deploy.sh`, which:

1. Reads `origin/main` and requires **green check runs** on that commit (GitHub
   API via the node’s existing `GH_TOKEN` in `~/.config/runner-dashboard/env`).
2. Downloads the matching **`dashboard-X.Y.Z.tar.gz`** release asset.
3. Installs the release into a unique temporary directory under
   `~/actions-runners/dashboard-releases/` while the old service remains up.
   Existing SHA release directories are preserved and cause a fail-closed
   collision error; auto-deploy never removes or overwrites them.
4. Rechecks activity immediately before stopping the service. The API summary
   and local staff SQLite store must both be readable and idle. Active work is
   skipped without waiting for a wall-clock drain period. The local check
   includes queued/preparing/running staff runs and non-user chat replies in
   `pending` or `streaming` delivery state.
5. Stops **system** or **user** `runner-dashboard`, carries the allowlisted
   mutable state below into the candidate, atomically makes it a SHA release,
   and switches the live path. A process lock prevents overlapping deploys.
6. Restarts the candidate and probes `/api/health` plus `GET
/api/staff/summary` (with `HUB_FLEET_TOKEN` when set). On failure, restores
   the exact prior target (including a migrated plain directory), restarts it,
   and confirms its health. The event is `rollback` only after restoration,
   restart, and health checks succeed;
   failed restoration is recorded as `rollback_failed` and retains release
   evidence for operator recovery.

**Hub-first:** nodes with `HUB_URL` wait until the hub’s `/api/fleet/status`
shows the same `deployment.git_sha` at the target commit.

**Staff safety:** deploy is skipped immediately while staff runs or active chat
replies exist; the next timer tick retries after they finish.

**Strict TDD for deploy/release changes (#2116):** Any PR modifying deploy scripts,
service units, release packaging, or auto-release workflows must first add a failing
regression test case reproducing the failure mode to the `#2116` deploy smoke test suite
(`tests/deploy/` / `tests/test_main_ci_release_wait.py`) before implementing the fix.

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

### State Classification and Verification

The service is stopped before the mutable-state snapshot, so SQLite databases
and their sidecars are copied together without concurrent service writes. The
candidate receives these operator/runtime paths when present:

- all of `config/` (operator-modified config is retained while new artifact
  defaults are added), `backend/data/`, and `backend/machine_registry.yml`;
- `agent_profiles.json`, `local_apps.json`, and root-level SQLite files and
  sidecars (`.db`, `.sqlite`, `.sqlite3` and `-wal`, `-shm`, `-journal`);
- root `.env` (retaining its private file mode) and matching runtime files
  (`*.db`, `*.sqlite*`, `*.ndjson`, `*.jsonl`, `.*_state.json`, and
  `*_history.json`) at nested runtime paths; immutable `.venv`, `venv`, `env`,
  `.cache`, `uv-bootstrap`, `wheels`, `node_modules`, `__pycache__`, and `.git`
  trees are excluded.

The new artifact's application code and `.venv` remain from the artifact; old
code is never overlaid. Existing machine credentials and service environment
under `~/.config/runner-dashboard/` remain at their stable external paths.
Auto-deploy fails closed if an allowlisted path contains a symlink or if the
activity database is missing, unreadable, or has an unknown schema.

After deployment, verify the live target and representative state paths:

```bash
readlink -f ~/actions-runners/dashboard
python3 -c 'import json,pathlib; p=pathlib.Path.home()/"actions-runners/dashboard/deployment.json"; print(json.loads(p.read_text())["git_sha"])'
test -d ~/actions-runners/dashboard/config
test -d ~/actions-runners/dashboard/backend/data
```

If the live path is still a plain directory, the first successful transaction
moves it to `dashboard-releases/legacy-pre-auto-deploy` before switching. If
that migration target already exists, the transaction refuses to rename or
overwrite anything. If a deployment lock remains after a process crash, inspect
its `pid=` record and confirm no deploy process is running before removing
`dashboard-releases/.node-auto-deploy.lock`.

Rollback restores the prior code target and its pre-switch state snapshot. If
the candidate started and wrote to its copied database before a health failure,
those writes remain in the failed release for operator review; auto-deploy does
not merge or downgrade database changes. Verify compatibility and reconcile
them before further recovery. An unhealthy restored release is reported as
`rollback_failed`, even when its prior filesystem target was restored.

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

## Attention When Green Main Goes Unreleased

`attention=true` is set (and surfaced via `/api/fleet/status`) when a gate-green main
commit has no release for more than `AUTO_DEPLOY_MAX_UNRELEASED_HOURS` (default 3), or more
than `AUTO_DEPLOY_MAX_GREEN_BEHIND` (default 5) green commits sit ahead of the newest
released one. Reason: `no release published for green main <sha> since <time>`. Usually the
release publisher (`Main CI auto-release`) is failing; fix that, not the node.
