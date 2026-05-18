#!/usr/bin/env bash
# deploy-host.sh — one-command full deploy of Runner_Dashboard on a host.
#
# Combines:
#   1. update-deployed.sh         (dashboard server + frontend + backend → ~/actions-runners/dashboard)
#   2. install-runner-maintenance.sh
#                                  - /usr/local/bin/runner-cleanup
#                                  - /usr/local/bin/runner-scheduler
#                                  - /usr/local/bin/runner-corruption-scan
#                                  - /usr/local/bin/heal-host
#                                  - /usr/local/bin/runner-hooks/{job-started,job-completed,force-drain}.sh
#                                  - systemd timers (cleanup, scheduler, corruption-scan)
#                                  - per-unit drop-ins (KillMode=mixed, hooks, force-drain)
#   3. Health verification        (/api/health, runner units, autoscaler)
#
# Idempotent. Safe to re-run.
#
# Usage:
#   bash deploy/deploy-host.sh                         # full deploy
#   bash deploy/deploy-host.sh --dry-run               # preview
#   bash deploy/deploy-host.sh --skip-pull             # skip 'git pull'
#   bash deploy/deploy-host.sh --no-restart-units      # apply drop-ins but don't restart actions.runner.* units
#
# Environment overrides:
#   REPO                  default: directory containing this script's parent
#   DEPLOY_DIR            default: $HOME/actions-runners/dashboard
#   AUTOSCALER_SERVICE    default: runner-autoscaler.service
#   DASHBOARD_SERVICE     default: runner-dashboard.service
#
# Exit codes: 0 success, non-zero on any failure (verbose).

set -Eeuo pipefail

# When the repo lives on /mnt/c/ (Windows-mounted), uv's default hardlink
# install mode hits a cross-filesystem copy that can intermittently fail
# with ENOENT on the .dist-info RECORD files. Force a plain copy so the
# deploy is robust regardless of where the checkout lives.
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${REPO:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
DEPLOY_DIR="${DEPLOY_DIR:-${HOME}/actions-runners/dashboard}"
AUTOSCALER_SERVICE="${AUTOSCALER_SERVICE:-runner-autoscaler.service}"
DASHBOARD_SERVICE="${DASHBOARD_SERVICE:-runner-dashboard.service}"
SKIP_PULL=0
NO_RESTART_UNITS=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)          DRY_RUN=1; shift ;;
        --skip-pull)        SKIP_PULL=1; shift ;;
        --no-restart-units) NO_RESTART_UNITS=1; shift ;;
        -h|--help)
            sed -n '1,30p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

ts() { date --iso-8601=seconds; }
section() { printf '\n\033[1;34m===\033[0m \033[1m%s\033[0m\n' "$*"; }
ok()      { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
warn()    { printf '\033[1;33m!\033[0m %s\n' "$*"; }
fail()    { printf '\033[1;31m✗\033[0m %s\n' "$*"; exit 1; }
run()     {
    if [[ "$DRY_RUN" == "1" ]]; then printf '\033[2m[dry-run]\033[0m %s\n' "$*"; return 0; fi
    "$@"
}

section "Preflight"
[[ -d "${REPO}/.git" ]] || fail "REPO=${REPO} is not a git checkout"
[[ -d "${REPO}/deploy" ]] || fail "REPO=${REPO}/deploy not found"
[[ -d "${DEPLOY_DIR}" ]] || fail "DEPLOY_DIR=${DEPLOY_DIR} missing — run setup.sh once first"
ok "repo=${REPO}"
ok "deploy_dir=${DEPLOY_DIR}"

# ── 1. Pull latest main ──────────────────────────────────────────────────────
if [[ "$SKIP_PULL" != "1" ]]; then
    section "Pulling latest main"
    cd "${REPO}"
    current_branch="$(git rev-parse --abbrev-ref HEAD)"
    if [[ "$current_branch" != "main" ]]; then
        warn "current branch is ${current_branch}, not main — pulling main into it via fast-forward"
    fi
    run git fetch origin main
    HEAD_BEFORE="$(git rev-parse HEAD)"
    run git pull --ff-only origin main || fail "git pull --ff-only failed; resolve manually"
    HEAD_AFTER="$(git rev-parse HEAD)"
    if [[ "$HEAD_BEFORE" == "$HEAD_AFTER" ]]; then
        ok "already at latest main: $(git log -1 --oneline)"
    else
        ok "advanced $(git log "${HEAD_BEFORE}..${HEAD_AFTER}" --oneline | wc -l) commit(s)"
        git log "${HEAD_BEFORE}..${HEAD_AFTER}" --oneline | sed 's/^/    /'
    fi
fi

# ── 2. Dashboard backend + frontend ──────────────────────────────────────────
section "Deploying dashboard (backend + frontend)"
if [[ "$DRY_RUN" == "1" ]]; then
    run "${SCRIPT_DIR}/update-deployed.sh" --repo "${REPO}" --deploy-dir "${DEPLOY_DIR}" --dry-run
else
    "${SCRIPT_DIR}/update-deployed.sh" --repo "${REPO}" --deploy-dir "${DEPLOY_DIR}"
fi

# ── 3. Runner maintenance (cleanup, scheduler, corruption-scan, hooks, heal) ─
section "Installing runner maintenance + hooks + heal-host"
if [[ "$DRY_RUN" == "1" ]]; then
    warn "dry-run: skipping install-runner-maintenance.sh (it has sudo writes)"
else
    "${SCRIPT_DIR}/install-runner-maintenance.sh"
fi

# ── 4. Optionally restart runner units to load any new drop-in changes ───────
if [[ "$NO_RESTART_UNITS" != "1" && "$DRY_RUN" != "1" ]]; then
    section "Restarting actions.runner.*.service units (load latest drop-ins)"
    # Use heal-host so workers drain via ExecStop=force-drain.sh
    if [[ -x /usr/local/bin/heal-host ]]; then
        sudo /usr/local/bin/heal-host || warn "heal-host returned non-zero; review output above"
    else
        warn "/usr/local/bin/heal-host missing; falling back to systemctl restart all"
        # Best-effort fallback
        units=$(systemctl list-unit-files --type=service --no-legend | awk '$1 ~ /^actions\.runner\..*\.service$/ {print $1}')
        for u in $units; do
            sudo systemctl restart "$u" || warn "restart $u failed"
        done
    fi
fi

# ── 5. Verify ────────────────────────────────────────────────────────────────
section "Verifying deploy"
[[ "$DRY_RUN" == "1" ]] && { ok "dry-run complete"; exit 0; }

# Dashboard health
if systemctl is-active --quiet "${DASHBOARD_SERVICE}"; then
    ok "${DASHBOARD_SERVICE}: active"
else
    fail "${DASHBOARD_SERVICE}: not active"
fi

HEALTH_URL="http://localhost:8321/api/health"
HEALTH_JSON="$(curl -s --max-time 5 "${HEALTH_URL}" 2>/dev/null || echo '{}')"
if [[ -n "${HEALTH_JSON}" && "${HEALTH_JSON}" != "{}" ]]; then
    GH_STATUS="$(printf '%s' "${HEALTH_JSON}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("github_api","unknown"))' 2>/dev/null || echo unknown)"
    RUNNERS_REG="$(printf '%s' "${HEALTH_JSON}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("runners_registered",0))' 2>/dev/null || echo 0)"
    ok "GET ${HEALTH_URL}: github_api=${GH_STATUS}  runners_registered=${RUNNERS_REG}"
else
    fail "GET ${HEALTH_URL}: empty response"
fi

# Autoscaler
if systemctl is-active --quiet "${AUTOSCALER_SERVICE}"; then
    ok "${AUTOSCALER_SERVICE}: active"
else
    warn "${AUTOSCALER_SERVICE}: not active — check 'journalctl -u ${AUTOSCALER_SERVICE} -n 30'"
fi

# Runner units
TOTAL=$(systemctl list-unit-files --type=service --no-legend | awk '$1 ~ /^actions\.runner\..*\.service$/ {n++} END{print n+0}')
ACTIVE=$(systemctl list-units --type=service --state=active --no-legend 'actions.runner.*.service' | awk '$1 ~ /^actions\.runner\./ {n++} END{print n+0}')
ok "actions.runner.*.service: ${ACTIVE}/${TOTAL} active"

# Lockfile hook activity (sanity check the hooks fire)
LOCK_COUNT=$(ls /var/run/runner-busy/*.lock 2>/dev/null | wc -l)
ok "/var/run/runner-busy: ${LOCK_COUNT} fresh lockfile(s)"

# Corruption residue (should be 0 after deploy+heal)
FC_COUNT=0
for d in "${HOME}/actions-runners"/runner-*; do
    [[ -d "$d/_work/_temp/_runner_file_commands" ]] && FC_COUNT=$((FC_COUNT+1))
done
if (( FC_COUNT == 0 )); then
    ok "stale _runner_file_commands dirs: 0"
else
    warn "stale _runner_file_commands dirs: ${FC_COUNT} — next cleanup pass will GC"
fi

# Cleanup textfile metric
if [[ -f /var/log/runner-cleanup/last-run.prom ]]; then
    ok "metrics: $(grep '^runner_cleanup_runs_total' /var/log/runner-cleanup/last-run.prom | head -1)"
fi

echo ""
ok "Deploy complete. Dashboard: http://localhost:8321"
