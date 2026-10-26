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
  (`attention: true` after rollback, hard failure, or release staleness; see [Release-staleness attention](#release-staleness-attention-green-main-no-release)).

## Release-Staleness Attention (Green Main, No Release)

Auto-deploy only deploys a main commit that is both CI-green **and** has a
published `dashboard-X.Y.Z` release. If main is green but the Main CI
auto-release gate never publishes, nodes silently sit on an old build. Each
cycle therefore also checks how long green main has gone unreleased and raises
`attention` when it exceeds a limit (RD #2932). Code:
`deploy/node_auto_deploy/staleness.py`, `scan_main_window` in
`deploy/node_auto_deploy/ci_gate.py`, and `evaluate_cycle` in
`deploy/node_auto_deploy/runner.py`.

### Knobs

| Variable                           | Default | Meaning                                                                                                       |
| ---------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------- |
| `AUTO_DEPLOY_MAX_UNRELEASED_HOURS` | `3`     | Attention when the newest green-but-unreleased main commit is older than this many hours (by committer date). |
| `AUTO_DEPLOY_MAX_GREEN_BEHIND`     | `5`     | Attention when more than this many green main commits (within the last 20 scanned) have no release.           |

Either condition is enough. Values must be positive numbers (the second is
truncated to an integer); an unset, empty, non-numeric, zero, or negative value
silently falls back to the default (`_env_number` in `deploy/node-auto-deploy.py`).

Set them in the node env file `~/.config/runner-dashboard/env`, the same file
that holds `GH_TOKEN`, `HUB_URL`, and `RUNNER_DASHBOARD_AUTO_DEPLOY`. The
systemd unit loads it with `EnvironmentFile=` (see
`deploy/systemd/runner-dashboard-auto-deploy.service`), so no unit edit or
reinstall is needed; the next timer tick picks the change up:

```bash
# ~/.config/runner-dashboard/env
AUTO_DEPLOY_MAX_UNRELEASED_HOURS=6
AUTO_DEPLOY_MAX_GREEN_BEHIND=8
```

### When Attention Is Raised

Each cycle walks the last 20 `main` commits newest-first until the first
green commit that has a release, and collects the green commits without one.
The newest of those is the _candidate_. Attention is raised only if a candidate
exists and it is too old or too many green commits are behind. A red main does
**not** raise this signal (it is reported as a CI gate failure instead), and
neither does a green main whose HEAD is already released.

The reason text has this exact format (`<sha7>` is the first 7 characters of
the newest green unreleased commit, `<iso>` its committer timestamp, and the
" since ..." part is omitted if the timestamp is unknown):

```text
no release published for green main <sha7> since <iso> (<N> green commit(s) behind)
```

The reason is prefixed to the cycle message, so it appears as
`<reason>; <original message>` in the state file and logs. The prefix is
applied to the `CI gate: ...` failure and to the
`already at <sha7> (main HEAD <sha7> awaiting CI/release)`. That last message,
and the `CI gate:` message when no deployable commit exists, now name the newest
main candidate (the HEAD or newest commit awaiting CI or release) so an operator
can see which commit is stuck without querying GitHub.

### Where It Shows Up

- `auto_deploy_state.json`: `attention: true`, with the reason in `message` and
  the newest event in `events`.
- `GET /api/fleet/status` -> `auto_deploy.attention` and `auto_deploy.message`
  on each node, served by `backend/node_auto_deploy_status.py` (best-effort
  read of the state file; a missing or unreadable file reports
  `attention: false`).

The flag is recomputed every cycle, so it clears by itself on the first cycle
where main is released or back under both limits.

### What to Check When It Fires

A green main with no release usually means the **Main CI auto-release** gate is
skipping (or failing) rather than a node problem.

1. Find the workflow run for the stuck commit: _Main CI auto-release_
   (`.github/workflows/main-ci-auto-release.yml`), which runs after the
   `CI Standard` and `Frontend Tests` workflows complete on `main`. Read the gate job log.
2. In `deploy/publish-main-ci-release.sh`, these log lines explain a missing
   release:
   - `CI gate has terminal failures for <sha>; skipping release`: a required
     check on that commit failed, so the publisher exits 0 without a release.
     Fix or re-run the failing check; the node scanner treats the same commit
     as not green.
   - `CI gate not satisfied for <sha> after N attempts; skipping release`: a
     check stayed pending past the wait window.
   - `... was superseded by <sha>` / `is not an ancestor of current main` /
     `is older than the latest auto-release`: the candidate was skipped on
     purpose; a newer commit should be released instead.
   - `CI gate response is invalid or incomplete ...; refusing release`: an
     error exit; investigate the check-runs payload.
3. Remember that the node scanner and the publisher share the same gate
   (`NON_BLOCKING_DEPLOY_CHECK_NAMES` in `ci_gate.py` lists the advisory checks
   both ignore). A check present in one view but not the other is a bug.
4. If the workflow never ran, check that `CI Standard` / `Frontend Tests`
   completed on that commit, then trigger it manually with `workflow_dispatch`
   and the full commit SHA.
5. Once a release exists the next timer tick deploys it with no node action.
   Do not raise the limits to silence the flag unless the delay is understood.

## Manual dry-run

```bash
bash ~/actions-runners/dashboard/deploy/node-auto-deploy.sh --dry-run
```
