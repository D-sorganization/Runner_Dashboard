#!/usr/bin/env bash
# deploy-check.sh — post-deploy validator for runner-dashboard on a host.
#
# Confirms the dashboard is not merely "active" (the service can start without
# actually working — see the leader-lock and registry-load regressions that
# silently broke federation for months). Fails if any of these are wrong:
#
#   1. /api/health returns status=healthy, github_api=connected
#   2. /api/diagnostics returns ok=true
#   3. Machine registry loaded (machines > 0)
#   4. Either MACHINE_ROLE != hub, OR FLEET_NODES is non-empty
#   5. Dashboard process is the leader (background tasks running)
#   6. All actions.runner.*.service units active
#   7. Hook lockfile dir exists and is writable for the runner user
#   8. Drop-ins from deploy/migrate-runner-units.sh are present on every unit
#
# Usage:
#   bash deploy/deploy-check.sh
#   bash deploy/deploy-check.sh --json     # machine-readable for monitoring
#   bash deploy/deploy-check.sh --quiet    # only errors
#
# Exit codes:
#   0   all checks passed
#   1   one or more checks failed (see stderr)
#   2   usage error
#
# This script is invoked automatically at the end of deploy-host.sh.

set -uo pipefail

JSON=0
QUIET=0
PORT="${DASHBOARD_PORT:-8321}"
HEALTH_URL="${HEALTH_URL:-http://localhost:${PORT}/api/health}"
DIAGNOSTICS_URL="${DIAGNOSTICS_URL:-http://localhost:${PORT}/api/diagnostics}"
TIMEOUT_SEC="${DEPLOY_CHECK_TIMEOUT:-15}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --json)  JSON=1; shift ;;
        --quiet) QUIET=1; shift ;;
        -h|--help)
            sed -n '1,30p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

# ── reporting helpers ────────────────────────────────────────────────────────
declare -a CHECK_NAMES=()
declare -a CHECK_STATUS=()  # PASS / FAIL / WARN
declare -a CHECK_DETAIL=()

record() {
    CHECK_NAMES+=("$1")
    CHECK_STATUS+=("$2")
    CHECK_DETAIL+=("$3")
}

green='\033[1;32m'; red='\033[1;31m'; yellow='\033[1;33m'; dim='\033[2m'; reset='\033[0m'

# ── 1. /api/health ───────────────────────────────────────────────────────────
HEALTH_JSON="$(curl -sS --max-time "$TIMEOUT_SEC" "$HEALTH_URL" 2>/dev/null || echo '{}')"
HEALTH_STATUS="$(printf '%s' "$HEALTH_JSON" | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin)
    print(d.get("status","missing"))
except Exception:
    print("invalid_json")' 2>/dev/null)"
HEALTH_GH="$(printf '%s' "$HEALTH_JSON" | python3 -c 'import sys,json
try:
    print(json.load(sys.stdin).get("github_api","unknown"))
except Exception:
    print("invalid_json")' 2>/dev/null)"
if [[ "$HEALTH_STATUS" == "healthy" && "$HEALTH_GH" == "connected" ]]; then
    record "health" PASS "status=$HEALTH_STATUS github_api=$HEALTH_GH"
elif [[ "$HEALTH_STATUS" == "healthy" ]]; then
    record "health" WARN "status=healthy but github_api=$HEALTH_GH"
else
    record "health" FAIL "status=$HEALTH_STATUS github_api=$HEALTH_GH"
fi

# ── 2. /api/diagnostics ──────────────────────────────────────────────────────
DIAG_JSON="$(curl -sS --max-time "$TIMEOUT_SEC" "$DIAGNOSTICS_URL" 2>/dev/null || echo '{}')"
DIAG_PYTHON=$(printf '%s' "$DIAG_JSON" | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin)
except Exception:
    print("invalid|||")
    sys.exit(0)
ok=d.get("ok")
reg=d.get("machine_registry",{})
fleet=d.get("fleet_federation",{})
leader=d.get("leader",{})
print(f"{ok}|{reg.get(\"loaded\")}|{reg.get(\"machines\",0)}|{reg.get(\"error\",\"\")}|{fleet.get(\"source\",\"\")}|{fleet.get(\"node_count\",0)}|{fleet.get(\"machine_role\",\"\")}|{leader.get(\"is_leader\",False)}|{leader.get(\"lock_path\",\"\")}")
' 2>/dev/null)
IFS='|' read -r DIAG_OK REG_LOADED REG_MACHINES REG_ERROR FLEET_SOURCE FLEET_NODE_COUNT MACHINE_ROLE LEADER_IS_LEADER LEADER_LOCK_PATH <<<"$DIAG_PYTHON"

if [[ "$DIAG_OK" == "True" ]]; then
    record "diagnostics" PASS "ok=true"
elif [[ -z "$DIAG_OK" || "$DIAG_OK" == "invalid" ]]; then
    record "diagnostics" FAIL "endpoint not reachable or returned invalid JSON"
else
    record "diagnostics" WARN "ok=false (see subsequent checks for cause)"
fi

# ── 3. Machine registry loaded ───────────────────────────────────────────────
if [[ "$REG_LOADED" == "True" && "${REG_MACHINES:-0}" -gt 0 ]]; then
    record "machine_registry" PASS "loaded $REG_MACHINES machine(s)"
elif [[ "$REG_LOADED" == "True" ]]; then
    record "machine_registry" WARN "loaded but contains 0 machines (empty registry)"
elif [[ -n "$REG_ERROR" && "$REG_ERROR" == *"world-writable"* ]]; then
    # Specific actionable message for the deploy-from-/mnt/c case.
    record "machine_registry" FAIL "world-writable YAML — run: chmod 0644 <path>; or redeploy (update-deployed.sh now normalises perms)"
else
    record "machine_registry" FAIL "load failed: ${REG_ERROR:-unknown error}"
fi

# ── 4. Fleet federation ──────────────────────────────────────────────────────
if [[ "$MACHINE_ROLE" == "hub" ]]; then
    if [[ "${FLEET_NODE_COUNT:-0}" -gt 0 ]]; then
        record "fleet_federation" PASS "$FLEET_NODE_COUNT peer(s) configured (source=$FLEET_SOURCE)"
    else
        record "fleet_federation" FAIL "hub has no FLEET_NODES peers — UI will only show this host"
    fi
else
    record "fleet_federation" PASS "role=$MACHINE_ROLE (not a hub; federation not required)"
fi

# ── 5. Leader status ─────────────────────────────────────────────────────────
if [[ "$LEADER_IS_LEADER" == "True" ]]; then
    record "leader" PASS "lock_path=${LEADER_LOCK_PATH:-?}"
else
    record "leader" WARN "running as follower (background audit + sync loops disabled)"
fi

# ── 6. Runner units ──────────────────────────────────────────────────────────
ACTIVE=$(systemctl list-units --type=service --state=active --no-legend 'actions.runner.*.service' 2>/dev/null | awk '$1 ~ /^actions\.runner\./ {n++} END{print n+0}')
TOTAL=$(systemctl list-unit-files --type=service --no-legend 2>/dev/null | awk '$1 ~ /^actions\.runner\..*\.service$/ {n++} END{print n+0}')
if (( TOTAL == 0 )); then
    record "runner_units" WARN "no actions.runner.*.service installed on this host"
elif (( ACTIVE == TOTAL )); then
    record "runner_units" PASS "$ACTIVE / $TOTAL active"
else
    record "runner_units" FAIL "only $ACTIVE / $TOTAL active"
fi

# ── 7. Lockfile dir ──────────────────────────────────────────────────────────
LOCK_DIR="${RUNNER_BUSY_LOCK_DIR:-/var/run/runner-busy}"
if [[ -d "$LOCK_DIR" ]]; then
    record "lockfile_dir" PASS "$LOCK_DIR exists"
else
    record "lockfile_dir" FAIL "$LOCK_DIR missing — JOB_STARTED hooks have nowhere to write"
fi

# ── 8. Per-unit drop-ins (#664) ──────────────────────────────────────────────
DROPIN_PATTERN='/etc/systemd/system/actions.runner.*.service.d/10-runner-dashboard-busy-lock.conf'
DROPIN_COUNT=$(ls $DROPIN_PATTERN 2>/dev/null | wc -l)
if (( TOTAL == 0 )); then
    record "dropins" PASS "no runner units → no drop-ins required"
elif (( DROPIN_COUNT >= TOTAL )); then
    record "dropins" PASS "$DROPIN_COUNT drop-in(s) for $TOTAL unit(s)"
else
    record "dropins" FAIL "only $DROPIN_COUNT drop-in(s) for $TOTAL unit(s) — run deploy/migrate-runner-units.sh"
fi

# ── reporting ────────────────────────────────────────────────────────────────
fail_count=0
warn_count=0
for s in "${CHECK_STATUS[@]}"; do
    [[ "$s" == "FAIL" ]] && fail_count=$((fail_count+1))
    [[ "$s" == "WARN" ]] && warn_count=$((warn_count+1))
done

if (( JSON == 1 )); then
    printf '{\n'
    printf '  "host": "%s",\n' "$(hostname)"
    printf '  "ok": %s,\n' "$([[ $fail_count -eq 0 ]] && echo true || echo false)"
    printf '  "fail_count": %d,\n' "$fail_count"
    printf '  "warn_count": %d,\n' "$warn_count"
    printf '  "checks": [\n'
    last=$(( ${#CHECK_NAMES[@]} - 1 ))
    for i in "${!CHECK_NAMES[@]}"; do
        sep=","; [[ $i -eq $last ]] && sep=""
        printf '    {"name": "%s", "status": "%s", "detail": "%s"}%s\n' \
            "${CHECK_NAMES[$i]}" "${CHECK_STATUS[$i]}" \
            "$(printf '%s' "${CHECK_DETAIL[$i]}" | sed 's/"/\\"/g')" "$sep"
    done
    printf '  ]\n}\n'
else
    if (( QUIET == 0 )); then
        printf '\n%s== Deploy check: %s ==%s\n' "$dim" "$(hostname)" "$reset"
    fi
    for i in "${!CHECK_NAMES[@]}"; do
        case "${CHECK_STATUS[$i]}" in
            PASS) color="$green"; sym="✓"; (( QUIET == 1 )) && continue ;;
            WARN) color="$yellow"; sym="!" ;;
            FAIL) color="$red"; sym="✗" ;;
            *)    color="$reset"; sym="?" ;;
        esac
        printf '%b%s%b %-22s %s\n' "$color" "$sym" "$reset" "${CHECK_NAMES[$i]}" "${CHECK_DETAIL[$i]}"
    done
    printf '\n'
    if (( fail_count > 0 )); then
        printf '%bFAILED:%b %d check(s) failed, %d warning(s)\n' "$red" "$reset" "$fail_count" "$warn_count" >&2
    else
        printf '%bOK:%b all checks passed (%d warnings)\n' "$green" "$reset" "$warn_count"
    fi
fi

(( fail_count == 0 )) && exit 0 || exit 1
