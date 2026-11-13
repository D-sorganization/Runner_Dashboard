# Runbook: `not uptodate; will not remove from working tree`

**Symptom class:** self-hosted jobs fail immediately after checkout, on files
the PR never touched.

Tracked at UpstreamDrift#9443, program Repository_Management#1505.

## What it looks like

`actions/checkout` reports (and is still marked **successful**):

```
##[error]error: Path 'src/config/tile_registry.py' not uptodate; will not remove from working tree.
##[error]error: Path 'scripts/registry/__init__.py' not uptodate; will not remove from working tree.
```

and the job then dies on a path that should exist, e.g.

```
python3: can't open file '.../scripts/ci/rehydrate_docker_context.py': [Errno 2] No such file or directory
Can't find 'action.yml' ... under '.../.github/actions/fetch-pinned-tools'
```

The failing paths are unrelated to the PR diff and vary run to run.

## Root cause

Two workspace conditions combine. Both leave `actions/checkout` reporting
**success**, which is why the failure surfaces one step later.

### A. Sparse-checkout left on with no patterns (the trigger)

`core.sparseCheckout=true` with an absent or empty `.git/info/sparse-checkout`
is an empty pattern set. An empty pattern set matches nothing, so
`unpack-trees` concludes **every** path belongs outside the working tree:
`git checkout --force <ref>` empties the tree and exits 0.

The quoted message is git's `WARNING_SPARSE_NOT_UPTODATE_FILE` — a _warning_
emitted on the sparse code path for paths it wanted to prune but could not.
It is not the ordinary `ERROR_NOT_UPTODATE_FILE` ("Entry '%s' not uptodate.
Cannot merge."), which would abort the checkout. That distinction is the tell:
if you see `Path '...' ... will not remove from working tree`, sparse-checkout
was active.

Verified on node-a (git 2.43): with `core.sparseCheckout=true` and an
empty pattern file, `git checkout --progress --force <ref>` exits 0 and leaves
the working tree empty.

`actions/checkout` runs `git sparse-checkout disable` and then unsets
`extensions.worktreeConfig`, which orphans the `.git/config.worktree` that
`disable` had just written `core.sparseCheckout=false` into — one way to land
in this state.

### B. A stale index stat cache (selects which paths warn)

A recursive `chown -R` / `chmod -R` over a runner's `_work` tree bumps the
**ctime** of every inode while leaving content, mtime, size and inode number
alone. Git's index caches per-file stat data, so afterwards `git status` and
`git reset --hard` re-hash content, see no change, report a clean tree — and
therefore **do not rewrite the index**.

Measured live on node-a runner-4: **13,220 of 13,224** tracked paths
stat-dirty, `git status --porcelain` empty, ctime diverged by ~17 minutes
while mtime, size and inode matched exactly.

The sweep hits **every runner on the host**, so the job that runs the `chown`
is usually _not_ the job that fails. Known offenders:

- `UpstreamDrift/.github/workflows/cross-engine-equivalence.yml` (2 sites)
- `Tools_Private/.github/workflows/ci-standard.yml` (2 sites)

all of the form `sudo chown -R $(id -u):$(id -g) /home/fleet-operator/actions-runners`.

## Confirming it

First, the sparse state:

```bash
W=/home/fleet-operator/actions-runners/runner-N/_work/<Repo>/<Repo>
git -C "$W" config --get core.sparseCheckout          # "true" is the trigger
ls -l "$W/.git/info/sparse-checkout"                  # absent/empty => matches nothing
```

Then the stat cache:

On the host, in the failing workspace:

```bash
W=/home/fleet-operator/actions-runners/runner-N/_work/<Repo>/<Repo>
git -C "$W" diff-files --name-only | wc -l   # large  -> stat cache is stale
git -C "$W" status --porcelain | wc -l       # 0      -> content is clean
git -C "$W" ls-files --debug -- <one-path>   # index ctime ...
stat -c '%Z %Y %i' "$W/<one-path>"           # ... disk ctime is LATER, mtime/ino equal
```

`ctime` diverging while `mtime`, `size` and `ino` match is the signature.

## Immediate remediation

```bash
# A: clear an incoherent sparse state (config on, no patterns)
git -C "$W" config --unset-all core.sparseCheckout
git -C "$W" config --unset-all index.sparse
rm -f "$W/.git/info/sparse-checkout"

# B: refresh the stat cache
git -C "$W" update-index -q --really-refresh
```

This re-stats every entry and rewrites the cached stat data where content
still matches. It never changes file content and never discards real local
modifications. Then re-run the failed jobs
(`gh run rerun <run-id> --failed`).

## Durable fixes

1. **Runner hook (this repo).** `deploy/runner-hooks/job-started.sh` heals
   `$GITHUB_WORKSPACE` before every job, so a poisoned workspace heals itself
   whatever caused it. Guarded to paths under a runner `_work` tree, bounded
   by `timeout`, never touches the network and always exits 0. See
   [Workspace residue from a previous job](#workspace-residue-from-a-previous-job)
   below for what it does. A sparse checkout is **not** left alone any more:
   at job start, any sparse state is residue from a previous job.
2. **Remove the sweeps.** Workflow steps must never run
   `sudo chown -R … /home/fleet-operator/actions-runners`. Scope ownership
   repairs to the specific directory that needs them — never a repository
   working tree, and never another runner's tree.

> **Do not** "fix" this by adding `chown -R` or `chmod -R` to a runner hook.
> That is the cause, not the cure: it would invalidate the stat cache on
> _every_ job rather than occasionally.

## Workspace residue from a previous job

Diagnosed 2026-10-17 on hub-a (Gasification_Model). Self-hosted runners
reuse `_work/<repo>/<repo>`, and three kinds of residue break the next job's
`actions/checkout`:

| Residue                                                                                                            | Symptom                                                                                                                                                                                                                                                                                  | Hook action                                                                                                                                                                                                            |
| ------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Sparse / skip-worktree state (`sparse-checkout:` in a previous job, worst with `sparse-checkout-cone-mode: false`) | `Path '...' not uptodate; will not remove from working tree`, then `Can't find 'action.yml' under .github/actions/...`                                                                                                                                                                   | Repaired **in place** when every index blob is present: `sparse-checkout disable`, drop the pattern file and config, clear skip-worktree bits, `checkout --force HEAD -- .`. A failed repair falls back to quarantine. |
| Partial clone (`--filter=blob:none`) with **missing** index blobs                                                  | Checkout's `git clean -ffdx` / `git reset --hard HEAD` run _before_ its auth extraheader is set, so each missing blob is lazy-fetched without credentials: `unable to read sha1 file of <path>`, then three `fatal: Authentication failed` per file; the step looks hung for 10+ minutes | **Quarantined**, then reseeded from a local sibling (below). `git sparse-checkout disable` does not fix this.                                                                                                          |
| Stale `.git/**.lock` from a killed job                                                                             | Checkout cannot lock the index                                                                                                                                                                                                                                                           | `*.lock` older than 1 minute under this workspace's own `.git` (including `.git/modules/**`) is removed.                                                                                                               |

A promisor clone whose index blobs are all present is **healthy** and is left
alone: once checkout's auth is set, later lazy fetches work. node-a (a
~6 Mbit/s uplink) runs 14 such workspaces. The blob check is local only:
`GIT_ALLOW_PROTOCOL=none` refuses every transport, including lazy fetches
(`GIT_NO_LAZY_FETCH` needs git 2.44; the fleet runs 2.34).

**Quarantine** renames the workspace (atomic, same filesystem) to
`<runner>/_work/_quarantine/<repo>-<epoch>-<pid>`, recreates the directory,
and deletes the old tree in a detached background process. Entries older than
an hour are re-reaped by later jobs; the nightly `runner-cleanup.sh` removes
anything left after that.

**Reseed.** An empty workspace means a network clone, and a herd of them
saturates the uplink (six GM workspaces at once took 43 minutes and were
cancelled). So the hook first rebuilds the workspace from the healthiest sibling
`<root>/*/_work/<repo>/<repo>` of another runner on the same host. A sibling
qualifies only with a valid HEAD, no promisor config, no sparse residue and no
`*.lock`; idle runners are preferred. The rebuild is `git clone --no-hardlinks
--no-checkout <sibling>`, `remote set-url origin https://github.com/<owner>/<repo>`,
a copy of `.git/modules` and `checkout --force HEAD`, which takes 11–14 s for GM.
If no sibling qualifies or any step fails, the directory is left empty. More than
3 quarantines on one host within 10 minutes logs a `quarantine herd` warning
(state in `$RUNNER_BUSY_LOCK_DIR/workspace-quarantines`).

Hook stderr lines to grep for in job logs: `[runner-cleanup] quarantined poisoned
workspace`, `reseeded ... from local sibling`, `repaired sparse-checkout residue
in place`, `removed stale workspace lock`.

Knobs: `RUNNER_HOOK_DISABLE_WORKSPACE_QUARANTINE=1` (repair only, never
quarantine), `RUNNER_HOOK_SEED_ROOT` (colon-separated roots to scan for
siblings), `RUNNER_HOOK_QUARANTINE_HERD_THRESHOLD` / `_WINDOW_SECONDS`.

## Hook wiring

The canonical hooks only help if the runner runs them:

- **Unit wiring.** `runner-hooks-restore.sh` (5-minute timer) gives every
  `actions.runner.*.service` whose effective Environment lacks the canonical
  `ACTIONS_RUNNER_HOOK_JOB_STARTED`/`_COMPLETED` a
  `<unit>.d/15-runner-hooks.conf` drop-in, then one `daemon-reload`.
  node-a's units had none. It never restarts a unit: the new environment
  applies at the next restart, which the nightly cleanup does only for idle
  runners.
- **`.env` overrides.** The runner's `<runner>/.env` wins over the unit. On
  hub-a every `.env` pointed `ACTIONS_RUNNER_HOOK_JOB_COMPLETED` at a
  legacy `fleet/runner-post-job.sh` that only cleared `_work/_temp`. The restore
  timer removes any hook override that is not the canonical path, after a
  timestamped `.env.bak.*`. `job-completed.sh` now clears `_work/_temp` itself
  (except `_runner_file_commands`).
- **Content drift.** The restore timer installs a hook only when it is
  **missing** (or re-chmods one that is not executable). It never rewrites one,
  because its source `/opt/runner-dashboard` can be months stale and would
  downgrade it. `refresh-maintenance-assets.sh`, run by every
  `update-deployed.sh`, refreshes installed hooks from the deployed repo copy.

Check a host without changing it:

```bash
sudo /usr/local/bin/runner-hooks-restore --check
bash deploy/refresh-maintenance-assets.sh --check
```

Metrics (`runner_hooks.prom`): `runner_hooks_unit_wiring_missing`,
`runner_hooks_unit_dropins_written`, `runner_hooks_env_overrides`,
`runner_hooks_env_overrides_removed`, `runner_hooks_restore_total`.

## Deploying a hook change

The hooks are read fresh per job, so **no runner restart is needed**. The normal
path is `update-deployed.sh`, which runs `refresh-maintenance-assets.sh`. By hand:

```bash
sudo -n install -m 0755 job-started.sh /usr/local/bin/runner-hooks/job-started.sh
```

Back up the existing file first, and never restart a unit whose
`pgrep -fc Runner.Worker` is non-zero.
