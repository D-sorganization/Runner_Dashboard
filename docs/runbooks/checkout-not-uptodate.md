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

The quoted message is git's `WARNING_SPARSE_NOT_UPTODATE_FILE` — a *warning*
emitted on the sparse code path for paths it wanted to prune but could not.
It is not the ordinary `ERROR_NOT_UPTODATE_FILE` ("Entry '%s' not uptodate.
Cannot merge."), which would abort the checkout. That distinction is the tell:
if you see `Path '...' ... will not remove from working tree`, sparse-checkout
was active.

Verified on ControlTower (git 2.43): with `core.sparseCheckout=true` and an
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

Measured live on ControlTower runner-4: **13,220 of 13,224** tracked paths
stat-dirty, `git status --porcelain` empty, ctime diverged by ~17 minutes
while mtime, size and inode matched exactly.

The sweep hits **every runner on the host**, so the job that runs the `chown`
is usually *not* the job that fails. Known offenders:

- `UpstreamDrift/.github/workflows/cross-engine-equivalence.yml` (2 sites)
- `Tools_Private/.github/workflows/ci-standard.yml` (2 sites)

all of the form `sudo chown -R $(id -u):$(id -g) /home/dieterolson/actions-runners`.

## Confirming it

First, the sparse state:

```bash
W=/home/dieterolson/actions-runners/runner-N/_work/<Repo>/<Repo>
git -C "$W" config --get core.sparseCheckout          # "true" is the trigger
ls -l "$W/.git/info/sparse-checkout"                  # absent/empty => matches nothing
```

Then the stat cache:

On the host, in the failing workspace:

```bash
W=/home/dieterolson/actions-runners/runner-N/_work/<Repo>/<Repo>
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

1. **Runner hook (this repo).** `deploy/runner-hooks/job-started.sh` clears
   both states against `$GITHUB_WORKSPACE` before every job, so a poisoned
   workspace heals itself whatever caused it. Guarded to paths under a runner
   `_work` tree and bounded by `timeout`. A *genuine* sparse checkout (config
   on **and** patterns present) is left untouched.
2. **Remove the sweeps.** Workflow steps must never run
   `sudo chown -R … /home/dieterolson/actions-runners`. Scope ownership
   repairs to the specific directory that needs them — never a repository
   working tree, and never another runner's tree.

> **Do not** "fix" this by adding `chown -R` or `chmod -R` to a runner hook.
> That is the cause, not the cure: it would invalidate the stat cache on
> *every* job rather than occasionally.

## Deploying a hook change

The hooks are read fresh per job, so **no runner restart is needed**:

```bash
sudo -n install -m 0755 job-started.sh /usr/local/bin/runner-hooks/job-started.sh
```

Back up the existing file first, and never restart a unit whose
`pgrep -fc Runner.Worker` is non-zero.
