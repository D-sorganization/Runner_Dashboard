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

# -- Runner scratch & cache preparation (Runner_Dashboard#1895, #1896) --------
# Self-hosted runners configure TMPDIR=<runner_dir>/_work/_tmp and
# PIP_CACHE_DIR=<runner_dir>/_work/_pip-cache (via configure-runner-tmpdir.sh
# and configure-runner-pipcache.sh in .env). If either was wiped by a host
# cleanup, recreate it before any workflow step runs.
if [ -n "${TMPDIR:-}" ]; then
    mkdir -m 0755 -p "$TMPDIR" 2>/dev/null || mkdir -p "$TMPDIR" 2>/dev/null || true
fi
if [ -n "${PIP_CACHE_DIR:-}" ]; then
    mkdir -m 0755 -p "$PIP_CACHE_DIR" 2>/dev/null || mkdir -p "$PIP_CACHE_DIR" 2>/dev/null || true
fi

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
#
# -- Workspace poisoning: repair or quarantine (2026-10-17) -------------------
# Clearing only that incoherent "on with no patterns" state was not enough.
# A self-hosted runner reuses `_work/<repo>/<repo>` across jobs, and three
# kinds of residue from a PREVIOUS job break the NEXT job's actions/checkout
# (diagnosed on DeskComputer, Gasification_Model):
#
#   1. Sparse / skip-worktree residue.  A job that ran actions/checkout with
#      `sparse-checkout:` (worst with `sparse-checkout-cone-mode: false`)
#      leaves core.sparseCheckout=true, .git/info/sparse-checkout and
#      thousands of skip-worktree index bits behind.  The next full checkout
#      fails with "Path '...' not uptodate; will not remove from working tree"
#      and then "Can't find 'action.yml' under .github/actions/...".
#   2. Partial clone with MISSING blobs.  `sparse-checkout:` (or
#      `filter: blob:none`) clones with --filter=blob:none.  The next checkout
#      runs `git clean -ffdx` and `git reset --hard HEAD` BEFORE it installs
#      its auth extraheader, so every blob the index references but the clone
#      never fetched is lazy-fetched WITHOUT credentials: "unable to read sha1
#      file of <path>" and then "fatal: Authentication failed" three times per
#      file.  Across thousands of files the step looks hung for 10+ minutes or
#      is cancelled.  `git sparse-checkout disable` does not fix this.
#      A promisor clone whose index blobs are all present is HEALTHY: once
#      checkout's auth is set, later lazy fetches work.  (ControlTower, on a
#      ~6 Mbit/s uplink, runs 14 such workspaces; re-cloning them would hurt.)
#   3. Stale git locks (.git/index.lock and friends, including under
#      .git/modules/**) left by a killed job, and the temp packs
#      (objects/pack/tmp_pack_*, tmp_idx_*) of a fetch it cancelled -- six on
#      DeskComputer runner-3, ~2.4 GB (#1979).
#
# The old exception -- "a genuine sparse checkout (config on AND patterns
# present) is left alone" -- was wrong for a shared workspace: at job start
# THIS job's checkout has not run yet, so any sparse state is foreign residue
# from a previous job.  The heal for $GITHUB_WORKSPACE is therefore, in order:
#
#   a. Remove *.lock older than 1 minute under this workspace's own .git.  A
#      runner runs one job at a time, so no live git process owns them.  (The
#      fleet-wide scan above stays opt-in.)
#   b. If the workspace is a partial clone or carries sparse residue, check
#      locally that every blob the index references is present.  If any is
#      missing -- or the check fails or times out -- QUARANTINE: rename the
#      workspace (atomic, same filesystem, never a cross-device copy) to
#      <runner>/_work/_quarantine/<repo>-<epoch>-<pid>, recreate the dir
#      (steps before checkout use it as their cwd) and delete the old tree in
#      a fully detached background process.  The new dir is RESEEDED from a
#      healthy local sibling workspace when one exists (see "Reseed" below),
#      so actions/checkout fetches a delta; otherwise it stays empty and
#      actions/checkout clones.
#   c. Sparse residue with every blob present: REPAIR IN PLACE (sparse-checkout
#      disable, drop the pattern file and config, clear skip-worktree bits,
#      `checkout --force HEAD -- .`).  Any failure falls back to quarantine.
#   d. A healthy promisor clone (no missing blobs, no sparse residue) is left
#      alone, as is a clean full clone (which is not even blob-checked).
#   e. The --really-refresh stat heal below.
#   f. Record HEAD as refs/runner-cache/last-checkout (#1979).  actions/checkout
#      deletes refs/heads/* and refs/remotes/* before it fetches, and a
#      detached HEAD is never offered as a "have", so the server assumed the
#      workspace held nothing and sent the FULL snapshot every job (GM:
#      250-540 MB per checkout; six at once starved the uplink into 30-min
#      checkouts).  One ref outside those namespaces lets the next fetch --
#      even --depth=1 into a shallow clone -- receive only the delta
#      (measured: 4.56 MiB -> 223 KiB).  A local update-ref; no network.
#
# No step may touch the network: GIT_ALLOW_PROTOCOL=none refuses every
# transport, including lazy fetches (GIT_NO_LAZY_FETCH only exists from git
# 2.44; the fleet runs 2.34).  Verified on git 2.34: `cat-file --batch-check`
# then reports an absent promisor blob as "missing" instead of fetching it.
#
# Everything is best-effort: every git and find call is bounded by `timeout`,
# nothing is fatal, and the hook still exits 0.  Quarantine entries whose
# background delete was interrupted are re-reaped after
# RUNNER_HOOK_QUARANTINE_MAX_AGE_SECONDS (1h); the nightly runner-cleanup.sh
# pass (root) removes anything left after that.
# RUNNER_HOOK_DISABLE_WORKSPACE_QUARANTINE=1 skips the quarantine (the
# in-place repair still runs).
_HOOK_GIT_TIMEOUT="${RUNNER_HOOK_GIT_TIMEOUT_SECONDS:-20}"
_HOOK_TREE_TIMEOUT="${RUNNER_HOOK_TREE_TIMEOUT_SECONDS:-120}"
_QUARANTINE_MAX_AGE_SECONDS="${RUNNER_HOOK_QUARANTINE_MAX_AGE_SECONDS:-3600}"

# _wsgit <workspace> <args...>: bounded git against one workspace.
_wsgit() {
    local ws="$1"
    shift
    timeout "$_HOOK_GIT_TIMEOUT" git -c safe.directory='*' -C "$ws" "$@"
}

# _partial_reason <workspace>: a promisor remote or partial-clone filter.
# `--file` means no repository discovery and no global/system config can
# leak into the verdict.
_partial_reason() {
    local gd="$1/.git" cfg
    for cfg in "$gd/config" "$gd/config.worktree"; do
        [ -f "$cfg" ] || continue
        if timeout "$_HOOK_GIT_TIMEOUT" git config --file "$cfg" --get-regexp \
                '^remote\..*\.partialclonefilter$' >/dev/null 2>&1 ||
           timeout "$_HOOK_GIT_TIMEOUT" git config --file "$cfg" --bool --get-regexp \
                '^remote\..*\.promisor$' 2>/dev/null | grep -q ' true$'; then
            printf 'partial clone'
            return 0
        fi
    done
}

# _sparse_reason <workspace>: sparse config, a pattern file or skip-worktree bits.
_sparse_reason() {
    local ws="$1" gd="$1/.git" reasons="" cfg
    for cfg in "$gd/config" "$gd/config.worktree"; do
        if [ -f "$cfg" ] && [ "$(timeout "$_HOOK_GIT_TIMEOUT" git config --file "$cfg" \
                --bool --get core.sparseCheckout 2>/dev/null)" = "true" ]; then
            reasons="${reasons:+$reasons, }core.sparseCheckout=true"
            break
        fi
    done
    if [ -e "$gd/info/sparse-checkout" ]; then
        reasons="${reasons:+$reasons, }.git/info/sparse-checkout present"
    fi
    if _wsgit "$ws" ls-files -v 2>/dev/null | grep -q '^[Ss] '; then
        reasons="${reasons:+$reasons, }skip-worktree entries"
    fi
    printf '%s' "$reasons"
}

# _blob_problem <workspace>: empty when every blob the index references is
# present locally; otherwise why not (missing blobs, or the check failed).
_blob_problem() {
    local ws="$1" out st missing
    out="$( { _wsgit "$ws" ls-files -s -z |
              sed -z -e '/^160000 /d' -e 's/^[0-7]* \([0-9a-f]*\) .*/\1/' |
              tr '\0' '\n' |
              _wsgit "$ws" cat-file --batch-check='%(objectname) %(objecttype)'
              echo "__status ${PIPESTATUS[*]}"; } 2>/dev/null )"
    st="${out##*__status }"
    if [ "$st" != "0 0 0 0" ]; then
        printf 'blob check failed or timed out (status %s)' "$st"
        return 0
    fi
    missing=$(printf '%s\n' "$out" | grep -c ' missing$' || true)
    if [ "${missing:-0}" -gt 0 ]; then
        printf '%s index blob(s) missing locally' "$missing"
    fi
}

# _repair_sparse_in_place <workspace>: returns 0 only when the workspace ends
# up a full, non-sparse checkout of HEAD.
_repair_sparse_in_place() {
    local ws="$1" gd="$1/.git" cfg st
    timeout "$_HOOK_TREE_TIMEOUT" git -c safe.directory='*' -C "$ws" \
        sparse-checkout disable >/dev/null 2>&1 || return 1
    rm -f "$gd/info/sparse-checkout" 2>/dev/null || return 1
    for cfg in "$gd/config" "$gd/config.worktree"; do
        [ -f "$cfg" ] || continue
        git config --file "$cfg" --unset-all core.sparseCheckout 2>/dev/null
        git config --file "$cfg" --unset-all core.sparseCheckoutCone 2>/dev/null
        git config --file "$cfg" --unset-all index.sparse 2>/dev/null
    done
    git config --file "$gd/config" --unset-all extensions.worktreeConfig 2>/dev/null
    _wsgit "$ws" ls-files -z | _wsgit "$ws" update-index -z --no-skip-worktree --stdin >/dev/null 2>&1
    st="${PIPESTATUS[*]}"
    [ "$st" = "0 0" ] || return 1
    timeout "$_HOOK_TREE_TIMEOUT" git -c safe.directory='*' -C "$ws" \
        checkout --force HEAD -- . >/dev/null 2>&1 || return 1
    # Verify rather than trust the exit codes.
    [ -z "$(_sparse_reason "$ws")" ]
}

# _reap_in_background <path>: delete <path> without delaying the job.  Fully
# detached: own session, no inherited stdio (the runner waits for the hook's
# pipes to close), cwd /, and RUNNER_TRACKING_ID dropped so the runner's
# end-of-job orphan-process cleanup does not kill the delete.
_reap_in_background() {
    (
        cd / 2>/dev/null || exit 0
        if command -v setsid >/dev/null 2>&1; then
            nohup env -u RUNNER_TRACKING_ID setsid nice -n 19 \
                rm -rf --one-file-system -- "$1" </dev/null >/dev/null 2>&1 &
        else
            nohup env -u RUNNER_TRACKING_ID nice -n 19 \
                rm -rf --one-file-system -- "$1" </dev/null >/dev/null 2>&1 &
        fi
    ) </dev/null >/dev/null 2>&1
}

# _reap_old_quarantine <qroot>: re-delete entries whose background delete was
# interrupted.  Age comes from the <epoch> in the entry NAME, because a rename
# does not reliably move a directory's mtime.  Bounded to 50 entries.
_reap_old_quarantine() {
    local qroot="$1" now entry base stamp n=0
    [ -d "$qroot" ] || return 0
    now=$(date +%s)
    for entry in "$qroot"/*; do
        [ -e "$entry" ] || continue
        n=$((n + 1))
        [ "$n" -le 50 ] || break
        base="${entry##*/}"
        stamp="${base%-*}"
        stamp="${stamp##*-}"
        case "$stamp" in ''|*[!0-9]*) continue ;; esac
        [ $((now - stamp)) -gt "$_QUARANTINE_MAX_AGE_SECONDS" ] || continue
        _reap_in_background "$entry"
    done
}

# -- Reseed from a local sibling (2026-10-17, production evidence) -----------
# An EMPTY workspace makes actions/checkout clone over the network.  At ~09:00Z
# six DeskComputer runners lost their GM workspaces at once; their parallel
# `fetch --depth=1` clones saturated the uplink, took 43 minutes and the jobs
# were cancelled (ControlTower is on ~6 Mbit/s cellular).  Rebuilding the same
# workspaces from a healthy sibling on local disk took 11-14 s each, after
# which checkout fetched only a small delta.  So a quarantined workspace is
# RESEEDED from the healthiest sibling workspace of the same repo on this host
# (<root>/*/_work/<repo>/<repo>, never this runner's own) before falling back
# to an empty dir:
#
#   git clone --no-hardlinks --no-checkout <sibling> <ws>
#   git remote set-url origin ${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}
#   cp -a <sibling>/.git/modules <ws>/.git/
#   git checkout --force HEAD
#
# A sibling qualifies only with a valid HEAD, no promisor/partial-clone config,
# no sparse residue, no skip-worktree bits and no *.lock under its .git.
# Siblings whose runner has no live Runner.Worker are preferred (a concurrent
# job could be mid-gc); `clone --local` only reads the source either way.
# Every step is bounded by `timeout` with network access disabled; any failure
# moves the half-built tree aside again and leaves the empty dir.
# RUNNER_HOOK_SEED_ROOT (colon-separated) overrides the scanned root(s).
_SEED_TIMEOUT="${RUNNER_HOOK_SEED_TIMEOUT_SECONDS:-120}"

# _seed_is_healthy <workspace>: a full, non-sparse, unlocked clone with a HEAD.
_seed_is_healthy() {
    local c="$1"
    [ -d "$c/.git" ] && [ ! -L "$c" ] && [ ! -L "$c/.git" ] || return 1
    _wsgit "$c" rev-parse -q --verify 'HEAD^{commit}' >/dev/null 2>&1 || return 1
    [ -z "$(_partial_reason "$c")" ] || return 1
    [ -z "$(timeout 10 find "$c/.git" -maxdepth 4 -name '*.lock' ! -path '*/objects/*' -print -quit 2>/dev/null)" ] || return 1
    [ -z "$(_sparse_reason "$c")" ]
}

# _runner_has_worker <runner_dir>: a Runner.Worker of that runner is alive.
# `bin` is a symlink to `bin.<version>`, and the worker's argv names the
# versioned dir, so match both literally (a pgrep regex on `<dir>/bin/` saw
# every runner as idle).
_runner_has_worker() {
    local dir="$1" cmd
    while IFS= read -r cmd; do
        case "$cmd" in
            "$dir"/bin/Runner.Worker* | "$dir"/bin.*/Runner.Worker*) return 0 ;;
        esac
    done < <(pgrep -af 'Runner\.Worker' 2>/dev/null | cut -d' ' -f2-)
    return 1
}

# _pick_seed <workspace>: print the best sibling workspace, or fail.
_pick_seed() {
    local ws="$1" own rel roots root c sib_runner busy_pick="" n=0
    own="${ws%%/_work/*}"
    rel="${ws#"$own"/}"
    roots="${RUNNER_HOOK_SEED_ROOT:-${own%/*}}"
    while IFS= read -r root; do
        [ -n "$root" ] || continue
        for c in "$root"/*/"$rel"; do
            [ -d "$c" ] || continue
            sib_runner="${c%%/_work/*}"
            [ "$sib_runner" != "$own" ] || continue
            n=$((n + 1))
            [ "$n" -le 32 ] || break 2
            _seed_is_healthy "$c" || continue
            if _runner_has_worker "$sib_runner"; then
                [ -n "$busy_pick" ] || busy_pick="$c"
                continue
            fi
            printf '%s' "$c"
            return 0
        done
    done < <(printf '%s\n' "$roots" | tr ':' '\n')
    [ -n "$busy_pick" ] || return 1
    printf '%s' "$busy_pick"
}

# _origin_url <seed>: the credential-free URL actions/checkout expects.
_origin_url() {
    local url
    if [ -n "${GITHUB_REPOSITORY:-}" ]; then
        printf '%s/%s' "${GITHUB_SERVER_URL:-https://github.com}" "$GITHUB_REPOSITORY"
        return 0
    fi
    url="$(git config --file "$1/.git/config" --get remote.origin.url 2>/dev/null || true)"
    case "$url" in
        https://*@*|http://*@*|'') return 1 ;;
    esac
    printf '%s' "$url"
}

# _reseed_workspace <workspace> <qroot>: rebuild the (empty) workspace from a
# local sibling.  Returns 0 on success; on failure the workspace is empty.
_reseed_workspace() {
    local ws="$1" qroot="$2" seed url started dest
    seed="$(_pick_seed "$ws")" || return 1
    url="$(_origin_url "$seed")" || return 1
    started=$(date +%s)
    # The local clone itself is the one transport allowed here: "file" covers
    # local paths; the seed is never a promisor, so nothing can lazy-fetch.
    if GIT_ALLOW_PROTOCOL="file" timeout "$_SEED_TIMEOUT" git -c safe.directory='*' clone -q --no-hardlinks --no-checkout \
            -- "$seed" "$ws" >/dev/null 2>&1 &&
       _wsgit "$ws" remote set-url origin "$url" >/dev/null 2>&1 &&
       { [ ! -d "$seed/.git/modules" ] ||
         timeout "$_SEED_TIMEOUT" cp -a "$seed/.git/modules" "$ws/.git/" 2>/dev/null; } &&
       timeout "$_SEED_TIMEOUT" git -c safe.directory='*' -C "$ws" checkout -q --force HEAD >/dev/null 2>&1 &&
       _wsgit "$ws" rev-parse -q --verify 'HEAD^{commit}' >/dev/null 2>&1; then
        echo "[runner-cleanup] reseeded $ws from local sibling $seed in $(( $(date +%s) - started ))s" >&2
        return 0
    fi
    echo "[runner-cleanup] reseed of $ws from $seed failed; leaving it empty" >&2
    dest="$qroot/${ws##*/}-$(date +%s)-$$-seed"
    if mv -T -- "$ws" "$dest" 2>/dev/null; then
        _reap_in_background "$dest"
    else
        rm -rf --one-file-system -- "$ws" 2>/dev/null || true
    fi
    mkdir -p "$ws" 2>/dev/null || true
    return 1
}

# _note_quarantine: herd guard.  Many quarantines at once mean something
# host-wide is poisoning workspaces (or this hook misjudges them), and every
# one costs a clone; still quarantine, but say so loudly.  State is a small
# file of epochs under the busy-lock dir, trimmed to the window.
_note_quarantine() {
    local state="$LOCK_DIR/workspace-quarantines" now window threshold recent
    now=$(date +%s)
    window="${RUNNER_HOOK_QUARANTINE_HERD_WINDOW_SECONDS:-600}"
    threshold="${RUNNER_HOOK_QUARANTINE_HERD_THRESHOLD:-3}"
    mkdir -p "$LOCK_DIR" 2>/dev/null || return 0
    if ! {
        if [ -f "$state" ]; then
            awk -v min=$((now - window)) '$1 >= min' "$state" 2>/dev/null
        fi
        echo "$now"
    } > "$state.tmp.$$" 2>/dev/null || ! mv -f "$state.tmp.$$" "$state" 2>/dev/null; then
        rm -f "$state.tmp.$$" 2>/dev/null
        return 0
    fi
    recent=$(wc -l < "$state" 2>/dev/null || echo 0)
    if [ "${recent:-0}" -gt "$threshold" ]; then
        echo "[runner-cleanup] WARNING: quarantine herd -- ${recent} workspaces quarantined on this host in the last ${window}s (threshold ${threshold}); check what is poisoning them before the uplink saturates" >&2
    fi
}

# _quarantine_workspace <workspace> <reason> <qroot>: returns 0 when the
# workspace was moved aside and replaced by a reseeded or empty directory.
_quarantine_workspace() {
    local ws="$1" reason="$2" qroot="$3" dest
    if [ "${RUNNER_HOOK_DISABLE_WORKSPACE_QUARANTINE:-0}" = "1" ] || [ -L "$ws" ]; then
        return 1
    fi
    mkdir -p "$qroot" 2>/dev/null || return 1
    # Same filesystem or nothing: a cross-device `mv` degrades into a
    # synchronous copy of the whole tree inside the job.
    [ "$(stat -c %d "$ws" 2>/dev/null)" = "$(stat -c %d "$qroot" 2>/dev/null)" ] || return 1
    dest="$qroot/${ws##*/}-$(date +%s)-$$"
    mv -T -- "$ws" "$dest" 2>/dev/null || return 1
    mkdir -p "$ws" 2>/dev/null || true
    echo "[runner-cleanup] quarantined poisoned workspace $ws (reason: $reason)" >&2
    _reap_in_background "$dest"
    _note_quarantine
    _reseed_workspace "$ws" "$qroot" || true
    return 0
}

# _remove_stale_workspace_locks <workspace>: *.lock older than 1 minute under
# this workspace's own .git (including .git/modules/**).
_remove_stale_workspace_locks() {
    local gd="$1/.git" lock
    [ -d "$gd" ] || return 0
    # (No -prune: -delete implies -depth, under which find ignores -prune.)
    timeout "${RUNNER_HOOK_LOCK_CLEANUP_TIMEOUT_SECONDS:-10}s" find "$gd" -maxdepth 8 \
        -type f -name '*.lock' ! -path '*/objects/*' -mmin +1 -print -delete 2>/dev/null | \
        while IFS= read -r lock; do
            echo "[runner-cleanup] removed stale workspace lock $lock" >&2
        done || true
}

# _remove_orphaned_fetch_packs <workspace>: temp packs older than 1 minute that a
# cancelled fetch left under this workspace's .git (including .git/modules/**).
_remove_orphaned_fetch_packs() {
    local gd="$1/.git" pack
    [ -d "$gd" ] || return 0
    timeout "${RUNNER_HOOK_LOCK_CLEANUP_TIMEOUT_SECONDS:-10}s" find "$gd" -maxdepth 8 \
        -type f -path '*/objects/pack/*' \( -name 'tmp_pack_*' -o -name 'tmp_idx_*' \) \
        -mmin +1 -print -delete 2>/dev/null | \
        while IFS= read -r pack; do
            echo "[runner-cleanup] removed orphaned fetch temp pack $pack" >&2
        done || true
}

# _record_negotiation_ref <workspace>: keep HEAD reachable from a ref that
# actions/checkout's ref cleanup never deletes, so the next fetch negotiates.
_record_negotiation_ref() {
    local ws="$1" head
    [ -d "$ws/.git" ] || return 0
    head="$(timeout 10 git -C "$ws" rev-parse --verify -q 'HEAD^{commit}' 2>/dev/null)" || return 0
    [ -n "$head" ] || return 0
    timeout 10 git -C "$ws" update-ref refs/runner-cache/last-checkout "$head" 2>/dev/null || true
}

_heal_workspace() {
    local ws="${1:-}" qroot partial sparse problem
    # Guard: only ever touch a path inside a runner _work tree.
    case "$ws" in
        */_work/*) ;;
        *) return 0 ;;
    esac
    case "$ws" in
        */../*|*/..|*/./*|*/_work/_quarantine|*/_work/_quarantine/*) return 0 ;;
    esac
    # The hook is its own process: these only affect the git calls below.
    # No inherited GIT_DIR may redirect them, and nothing may reach a network.
    unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY
    export GIT_ALLOW_PROTOCOL=none GIT_NO_LAZY_FETCH=1 GIT_TERMINAL_PROMPT=0
    # Even `git config --file` discovers a repository from the cwd and dies
    # inside a broken one -- and the cwd may be the very workspace being
    # quarantined.  Everything below uses absolute paths.
    cd / 2>/dev/null || true
    qroot="${ws%%/_work/*}/_work/_quarantine"
    _reap_old_quarantine "$qroot"
    [ -d "$ws/.git" ] || [ -f "$ws/.git" ] || return 0
    if [ -d "$ws/.git" ]; then
        _remove_stale_workspace_locks "$ws"                                 # a
        _remove_orphaned_fetch_packs "$ws"
        partial="$(_partial_reason "$ws")"
        sparse="$(_sparse_reason "$ws")"
        if [ -n "$partial$sparse" ]; then
            problem="$(_blob_problem "$ws")"                                # b
            if [ -n "$problem" ]; then
                if _quarantine_workspace "$ws" "$problem${partial:+; $partial}${sparse:+; $sparse}" "$qroot"; then
                    _record_negotiation_ref "$ws"                           # f (reseeded)
                    return 0
                fi
                echo "[runner-cleanup] could not quarantine poisoned workspace $ws ($problem)" >&2
            elif [ -n "$sparse" ]; then                                     # c
                if _repair_sparse_in_place "$ws"; then
                    echo "[runner-cleanup] repaired sparse-checkout residue in place in $ws ($sparse)" >&2
                elif _quarantine_workspace "$ws" "in-place sparse repair failed; $sparse" "$qroot"; then
                    return 0
                else
                    echo "[runner-cleanup] could not repair or quarantine sparse residue in $ws ($sparse)" >&2
                fi
            fi                                                              # d
        fi
    fi
    # e. Bounded: never hang a job on a pathological workspace.
    timeout 120 git -C "$ws" update-index -q --really-refresh 2>/dev/null || true
    _record_negotiation_ref "$ws"                                           # f
}
if [ -n "${GITHUB_WORKSPACE:-}" ]; then
    _heal_workspace "$GITHUB_WORKSPACE" || true
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
