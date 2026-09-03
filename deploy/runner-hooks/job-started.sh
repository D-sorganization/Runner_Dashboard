#!/usr/bin/env bash
# Runner job-started hook (ACTIONS_RUNNER_HOOK_JOB_STARTED).
#
# Writes a sentinel lockfile that both the autoscaler
# (backend/runner_autoscaler.py) and the nightly cleanup
# (deploy/runner-cleanup.sh) consult before stopping a runner.
#
# Background — issue #651:
# `_runner_is_busy()` looks for a `Runner.Worker` child of the unit's
# MainPID. There is a brief window (~1-2s) between job pickup by the
# Listener and the Worker fork during which a job IS assigned to the
# runner but no Worker process exists yet. If the autoscaler runs in
# that window, it kills the Listener and leaves residue in
# `_work/_temp/_runner_file_commands/` and `_diag/pages/*.log`.
#
# This hook closes the race on the *post*-fork side: as soon as the
# Worker is alive enough to execute hooks, it touches the lockfile.
# The autoscaler/cleanup busy-check returns True whenever the file
# exists, so even a transient psutil hiccup that misses the Worker
# child still results in a safe "busy" verdict.
#
# Defense-in-depth: this does not replace the cgroup-based busy check
# (Tasks count in the unit's cgroup); it adds a second independent
# signal.

set -u

LOCK_DIR="${RUNNER_BUSY_LOCK_DIR:-/var/run/runner-busy}"
RUNNER_NAME="${RUNNER_NAME:-${HOSTNAME}-unknown}"
HOME_DIR="${HOME:-/home/$USER}"

# -- Stale-git-lock cleanup (Runner_Dashboard#640) ---------------------------
# Before this job's actions/checkout runs, scrub leftover lock files from a
# prior job that was killed mid-`git config --global` (typical cause: the
# autoscaler stopped a busy runner). One orphaned ~/.gitconfig.lock breaks
# every runner sharing this HOME with cryptic "could not lock config file"
# at the very first step of checkout. Best-effort — never exits non-zero.
for _gclock in "$HOME_DIR/.gitconfig.lock" "$HOME_DIR/.gitconfig.lock.0"; do
    if [ -f "$_gclock" ]; then
        rm -f "$_gclock" 2>/dev/null && \
            echo "[runner-cleanup] removed stale $_gclock" >&2
    fi
done
# Per-worktree lock scans are intentionally opt-in. On large SSD runner pools,
# an unbounded pre-job find can spend minutes in disk wait and cancel the job
# before the workflow starts. Keep this cleanup in scheduled maintenance by
# default; enable here only for one-off recovery.
_work_root="$HOME_DIR/actions-runners"
if [ "${RUNNER_HOOK_ENABLE_WORKTREE_LOCK_CLEANUP:-0}" = "1" ] && [ -d "$_work_root" ]; then
    timeout "${RUNNER_HOOK_LOCK_CLEANUP_TIMEOUT_SECONDS:-10}s" find "$_work_root" \
        \( -path '*/.git/index.lock' -o \
           -path '*/.git/HEAD.lock' -o \
           -path '*/.git/config.lock' -o \
           -path '*/.git/packed-refs.lock' \) \
        -mmin +1 -print -delete 2>/dev/null | \
        while IFS= read -r _l; do
            echo "[runner-cleanup] removed stale worktree lock $_l" >&2
        done || true
fi
# -- Workspace checkout-integrity heal (UpstreamDrift#9443) ------------------
# Two independent ways a self-hosted workspace poisons the NEXT job's
# actions/checkout. Both make checkout report success while leaving a
# half-populated (or empty) working tree, so the job dies on a missing path --
# e.g. "scripts/ci/rehydrate_docker_context.py: No such file or directory" or
# "Can't find 'action.yml' under .github/actions/fetch-pinned-tools" -- on
# files the PR never touched.
# A recursive `chown -R`/`chmod -R` anywhere over a runner's _work tree bumps
# the ctime of every inode WITHOUT changing content, mtime, size or inode
# number.  Git's index caches per-file stat data, so afterwards every tracked
# path looks stat-dirty while `git status` still reports a clean tree (status
# re-hashes content; it does not rewrite the index).
#
# The next job's actions/checkout then runs `git checkout --force <ref>`, whose
# verify_uptodate() consults *stat only*.  Every file the new ref deletes is
# rejected with
#     error: Path '<p>' not uptodate; will not remove from working tree.
# and the checkout leaves a HALF-POPULATED tree: stale files survive and new
# files are never written.  The job then dies on a missing path -- e.g.
# "scripts/ci/rehydrate_docker_context.py: No such file or directory" or
# "Can't find 'action.yml' under .github/actions/fetch-pinned-tools".
#
# `git update-index --really-refresh` re-stats every entry and, where the
# content still matches, rewrites the cached stat data -- restoring the
# invariant actions/checkout depends on.  It never touches file content and
# never discards real local modifications.
#
# NOTE (deliberate): this hook must NEVER `chown -R` or `chmod -R` the
# workspace itself.  That is the *cause* of this failure mode, not a remedy --
# doing it here would invalidate the stat cache on every single job.
# The other half of the same failure is a workspace left with sparse-checkout
# still switched ON but with no surviving pattern file.  An empty pattern set
# matches nothing, so unpack-trees concludes every path belongs OUTSIDE the
# working tree: `git checkout --force` then *empties the tree*, emitting the
# WARNING_SPARSE_NOT_UPTODATE_FILE warning above for the paths whose stat
# cache is stale and silently deleting the rest -- while still exiting 0, so
# actions/checkout reports success.  Verified on ControlTower: with
# `core.sparseCheckout=true` and an empty `.git/info/sparse-checkout`,
# `git checkout --progress --force <ref>` exits 0 and leaves the tree empty.
#
# actions/checkout runs `git sparse-checkout disable` and then unsets
# `extensions.worktreeConfig`, which orphans the `.git/config.worktree` that
# `disable` had just written `core.sparseCheckout=false` into.  Clearing the
# state outright at job start makes that sequence a no-op.
_neutralise_sparse_checkout() {
    _ws="$1"
    _sparse=$(git -C "$_ws" config --get core.sparseCheckout 2>/dev/null || true)
    _patterns="$_ws/.git/info/sparse-checkout"
    # A genuine sparse checkout (config on AND patterns present) is left
    # alone -- only the incoherent "on with no patterns" state is cleared.
    if [ "$_sparse" = "true" ] && [ ! -s "$_patterns" ]; then
        echo "[runner-cleanup] clearing incoherent sparse-checkout state in $_ws" >&2
        git -C "$_ws" config --unset-all core.sparseCheckout 2>/dev/null || true
        git -C "$_ws" config --unset-all core.sparseCheckoutCone 2>/dev/null || true
        git -C "$_ws" config --unset-all index.sparse 2>/dev/null || true
        rm -f "$_patterns" 2>/dev/null || true
    fi
}

_heal_workspace() {
    _ws="${1:-}"
    # Guard: only ever touch a path inside a runner _work tree.
    case "$_ws" in
        */_work/*) ;;
        *) return 0 ;;
    esac
    [ -d "$_ws/.git" ] || [ -f "$_ws/.git" ] || return 0
    _neutralise_sparse_checkout "$_ws"
    # Bounded: never hang a job on a pathological workspace.
    timeout 120 git -C "$_ws" update-index -q --really-refresh 2>/dev/null || true
}
if [ -n "${GITHUB_WORKSPACE:-}" ]; then
    _heal_workspace "$GITHUB_WORKSPACE"
fi
# -- end cleanup -------------------------------------------------------------

# Best-effort directory creation. Hook runs as the runner user; the
# directory should be group-writable for that user (installer ensures it).
mkdir -p "$LOCK_DIR" 2>/dev/null || true

LOCK_FILE="${LOCK_DIR}/${RUNNER_NAME}.lock"

# Atomic write with metadata so observers can see who/what claimed it.
{
    printf 'pid=%s\n' "$$"
    printf 'runner=%s\n' "$RUNNER_NAME"
    printf 'job=%s\n' "${GITHUB_JOB:-unknown}"
    printf 'workflow=%s\n' "${GITHUB_WORKFLOW:-unknown}"
    printf 'run_id=%s\n' "${GITHUB_RUN_ID:-unknown}"
    printf 'repository=%s\n' "${GITHUB_REPOSITORY:-unknown}"
    printf 'started_at=%s\n' "$(date --iso-8601=seconds)"
} > "$LOCK_FILE.tmp" 2>/dev/null && mv "$LOCK_FILE.tmp" "$LOCK_FILE" 2>/dev/null

# Hooks must exit 0 to avoid failing the job.
exit 0
