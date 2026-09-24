#!/usr/bin/env bash
# staff-node-acceptance.sh — Unified acceptance check for Staff Hub nodes (Runner_Dashboard#1273).
#
# Validates node-readiness across DeskComputer (scheduler), OGLaptop (worker), and ControlTower (worker).
# Checks:
#   1. Deployment: Dashboard HTTP health endpoint and service active
#   2. Sign-ins: GitHub CLI auth status
#   3. CLIs: Node LTS and provider CLIs (claude, codex, agy, cursor-agent)
#   4. Drop-in: systemd staff-hub.conf permissions and required directory paths
#   5. Role source and timer: live RM clone, roles dir, staff.gitconfig, rm-sync timer, user lingering
#   6. Holds: no worker roles blocked by active holds
#   7. Scheduler: matches expected mode (worker: 0, scheduler: 1)
#   8. Ollama: local/gateway reachability and version check
#   9. Providers: local provider flags on /api/staff/board, and optional ad-hoc run verification
set -Eeuo pipefail

PORT="8321"
HOST="127.0.0.1"
ROLE=""
ENV_FILE="${HOME}/.config/runner-dashboard/env"
DROPIN_FILE="/etc/systemd/system/runner-dashboard.service.d/staff-hub.conf"
SKIP_SERVICE=0
SKIP_NETWORK=0
RUN_AD_HOC=0
VERBOSE=0

usage() {
    cat <<EOF
Usage: staff-node-acceptance.sh [OPTIONS]

Options:
  --port PORT           Dashboard port (default: 8321)
  --host HOST           Dashboard host (default: 127.0.0.1)
  --role ROLE           Expected node role: 'worker' or 'scheduler'
                        (default: auto-detected from hostname; DeskComputer=scheduler, others=worker)
  --env-file PATH       Path to runner-dashboard environment file (default: ~/.config/runner-dashboard/env)
  --dropin-file PATH    Path to systemd drop-in file (default: /etc/systemd/system/runner-dashboard.service.d/staff-hub.conf)
  --skip-service        Skip systemctl service and user timer checks (useful in non-systemd test environments)
  --skip-network        Skip Ollama network reachability check
  --run-ad-hoc          Run live ad-hoc health runs for each configured provider
  -v, --verbose         Verbose output
  -h, --help            Show this help message and exit
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port) PORT="${2:?--port requires an argument}"; shift 2 ;;
        --host) HOST="${2:?--host requires an argument}"; shift 2 ;;
        --role) ROLE="${2:?--role requires an argument}"; shift 2 ;;
        --env-file) ENV_FILE="${2:?--env-file requires an argument}"; shift 2 ;;
        --dropin-file) DROPIN_FILE="${2:?--dropin-file requires an argument}"; shift 2 ;;
        --skip-service) SKIP_SERVICE=1; shift ;;
        --skip-network) SKIP_NETWORK=1; shift ;;
        --run-ad-hoc) RUN_AD_HOC=1; shift ;;
        -v|--verbose) VERBOSE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

# Auto-detect role if not explicitly provided
if [[ -z "$ROLE" ]]; then
    HOSTNAME_LOWER="$(hostname 2>/dev/null | tr '[:upper:]' '[:lower:]' || echo "")"
    if [[ "$HOSTNAME_LOWER" == *"deskcomputer"* ]] || [[ "$HOSTNAME_LOWER" == *"desktop"* ]]; then
        ROLE="scheduler"
    else
        ROLE="worker"
    fi
fi

if [[ "$ROLE" != "worker" && "$ROLE" != "scheduler" ]]; then
    echo "ERROR: Invalid role '$ROLE'. Must be 'worker' or 'scheduler'." >&2
    exit 2
fi

EXPECTED_SCHEDULER="0"
if [[ "$ROLE" == "scheduler" ]]; then
    EXPECTED_SCHEDULER="1"
fi

PASS_COUNT=0
FAIL_COUNT=0
TOTAL_COUNT=0

report_pass() {
    local name="$1"
    local detail="${2:-}"
    TOTAL_COUNT=$((TOTAL_COUNT + 1))
    PASS_COUNT=$((PASS_COUNT + 1))
    if [[ -n "$detail" ]]; then
        printf '  [PASS] %s (%s)\n' "$name" "$detail"
    else
        printf '  [PASS] %s\n' "$name"
    fi
}

report_fail() {
    local name="$1"
    local detail="${2:-}"
    TOTAL_COUNT=$((TOTAL_COUNT + 1))
    FAIL_COUNT=$((FAIL_COUNT + 1))
    if [[ -n "$detail" ]]; then
        printf '  [FAIL] %s: %s\n' "$name" "$detail" >&2
    else
        printf '  [FAIL] %s\n' "$name" >&2
    fi
}

log_verbose() {
    if [[ "$VERBOSE" == "1" ]]; then
        printf '    [DEBUG] %s\n' "$*"
    fi
}

printf "=== Staff Node Acceptance Check ===\n"
printf "Node Hostname: %s\n" "$(hostname 2>/dev/null || echo 'unknown')"
printf "Expected Role: %s (STAFF_SCHEDULER_ENABLED=%s)\n" "$ROLE" "$EXPECTED_SCHEDULER"
printf "Dashboard API: http://%s:%s\n\n" "$HOST" "$PORT"

# -----------------------------------------------------------------------------
# 1. Deployment Check
# -----------------------------------------------------------------------------
printf "1. Deployment & Health API\n"

if [[ "$SKIP_SERVICE" != "1" ]]; then
    if systemctl is-active runner-dashboard >/dev/null 2>&1; then
        report_pass "runner-dashboard systemd service is active"
    else
        report_fail "runner-dashboard systemd service is not active" "$(systemctl is-active runner-dashboard 2>&1 || true)"
    fi
fi

HEALTH_JSON=""
if HEALTH_JSON="$(curl -fsS --max-time 5 "http://${HOST}:${PORT}/api/health" 2>/dev/null)"; then
    GIT_SHA="$(printf '%s' "$HEALTH_JSON" | grep -o '"git_sha":"[^"]*' | cut -d'"' -f4 || echo "unknown")"
    STATUS="$(printf '%s' "$HEALTH_JSON" | grep -o '"status":"[^"]*' | cut -d'"' -f4 || echo "unknown")"
    if [[ "$STATUS" == "ok" || "$STATUS" == "healthy" ]]; then
        report_pass "Dashboard API health check" "status=${STATUS}, git_sha=${GIT_SHA}"
    else
        report_fail "Dashboard API health reports non-ok" "status=${STATUS}"
    fi
else
    report_fail "Dashboard API health check unreachable" "http://${HOST}:${PORT}/api/health"
fi

# -----------------------------------------------------------------------------
# 2. Sign-ins Check
# -----------------------------------------------------------------------------
printf "\n2. Sign-ins & Identity\n"

if command -v gh >/dev/null 2>&1; then
    GH_OUT=""
    if GH_OUT="$(env -u GH_TOKEN -u GITHUB_TOKEN gh auth status 2>&1)"; then
        report_pass "GitHub CLI authentication" "gh auth status OK"
    else
        # In some versions gh auth status writes to stderr but exits 0
        if printf '%s' "$GH_OUT" | grep -qi "Logged in to github.com"; then
            report_pass "GitHub CLI authentication" "Logged in"
        else
            report_fail "GitHub CLI not logged in" "$GH_OUT"
        fi
    fi
else
    report_fail "GitHub CLI not installed or not in PATH" "gh command missing"
fi

# -----------------------------------------------------------------------------
# 3. CLIs & Runtimes Check
# -----------------------------------------------------------------------------
printf "\n3. Provider CLIs & Tooling\n"

if command -v node >/dev/null 2>&1; then
    NODE_VER="$(node --version 2>/dev/null || echo "")"
    report_pass "Node runtime" "$NODE_VER"
else
    report_fail "Node runtime" "node not found in PATH"
fi

for cli in claude codex agy cursor-agent; do
    if command -v "$cli" >/dev/null 2>&1; then
        report_pass "CLI present: ${cli}" "$(command -v "$cli")"
    else
        report_fail "CLI missing: ${cli}" "not found in PATH"
    fi
done

# -----------------------------------------------------------------------------
# 4. Service Drop-in Check
# -----------------------------------------------------------------------------
printf "\n4. Service Drop-in & Filesystem Access\n"

if [[ -f "$DROPIN_FILE" ]]; then
    report_pass "Drop-in file exists" "$DROPIN_FILE"
    
    if grep -q "MemoryDenyWriteExecute=false" "$DROPIN_FILE"; then
        report_pass "MemoryDenyWriteExecute is false"
    else
        report_fail "MemoryDenyWriteExecute is not false in drop-in"
    fi

    # Required paths in ReadWritePaths
    DROPIN_RW="$(grep '^ReadWritePaths=' "$DROPIN_FILE" | tr '\n' ' ' || echo "")"
    for req in ".claude" ".codex" ".gemini" ".antigravity" "staff-repos" "staff-worktrees" ".cursor"; do
        if printf '%s' "$DROPIN_RW" | grep -q "$req"; then
            report_pass "Drop-in permits: ${req}"
        else
            report_fail "Drop-in missing ReadWritePaths entry: ${req}"
        fi
    done
else
    if [[ "$SKIP_SERVICE" == "1" ]]; then
        report_pass "Drop-in file check skipped" "--skip-service enabled"
    else
        report_fail "Drop-in file missing" "$DROPIN_FILE"
    fi
fi

# Check required staff directories exist in HOME
for dir in "${HOME}/staff-repos" "${HOME}/staff-worktrees" "${HOME}/.config/runner-dashboard"; do
    if [[ -d "$dir" ]]; then
        report_pass "Directory exists: ${dir}"
    else
        report_fail "Directory missing: ${dir}"
    fi
done

# -----------------------------------------------------------------------------
# 5. Role Source & Timer Check
# -----------------------------------------------------------------------------
printf "\n5. Live Role Source & Timer\n"

if [[ -f "$ENV_FILE" ]]; then
    report_pass "Service env file exists" "$ENV_FILE"
    
    RM_ROOT="$(grep '^STAFF_RM_ROOT=' "$ENV_FILE" | cut -d'=' -f2- | tr -d '"'"'" || echo "")"
    ROLES_DIR="$(grep '^STAFF_ROLES_DIR=' "$ENV_FILE" | cut -d'=' -f2- | tr -d '"'"'" || echo "")"
    GIT_CFG="$(grep '^GIT_CONFIG_GLOBAL=' "$ENV_FILE" | cut -d'=' -f2- | tr -d '"'"'" || echo "")"

    if [[ -n "$RM_ROOT" && -d "$RM_ROOT" ]]; then
        report_pass "STAFF_RM_ROOT valid directory" "$RM_ROOT"
    else
        report_fail "STAFF_RM_ROOT invalid or missing" "$RM_ROOT"
    fi

    if [[ -n "$ROLES_DIR" && -d "$ROLES_DIR" ]]; then
        ROLE_COUNT="$(find "$ROLES_DIR" -maxdepth 1 -name "*.yml" 2>/dev/null | wc -l || echo "0")"
        if [[ "$ROLE_COUNT" -gt 0 ]]; then
            report_pass "STAFF_ROLES_DIR contains roles" "$ROLE_COUNT roles found"
        else
            report_fail "STAFF_ROLES_DIR contains no *.yml files" "$ROLES_DIR"
        fi
    else
        report_fail "STAFF_ROLES_DIR invalid or missing" "$ROLES_DIR"
    fi

    if [[ -n "$GIT_CFG" && -f "$GIT_CFG" ]]; then
        report_pass "GIT_CONFIG_GLOBAL valid file" "$GIT_CFG"
    else
        report_fail "GIT_CONFIG_GLOBAL invalid or missing" "$GIT_CFG"
    fi
else
    report_fail "Service env file missing" "$ENV_FILE"
fi

if [[ "$SKIP_SERVICE" != "1" ]]; then
    if systemctl --user is-active runner-dashboard-rm-sync.timer >/dev/null 2>&1; then
        report_pass "runner-dashboard-rm-sync.timer is active"
    else
        # Fallback check system journal for timer
        if journalctl --user -u runner-dashboard-rm-sync.timer -n 5 2>/dev/null | grep -qi "Started"; then
            report_pass "runner-dashboard-rm-sync.timer recorded in journal"
        else
            report_fail "runner-dashboard-rm-sync.timer not active"
        fi
    fi

    LINGER_STATUS="$(loginctl show-user "$USER" -p Linger 2>/dev/null || echo "")"
    if [[ "$LINGER_STATUS" == *"Linger=yes"* ]] || [[ -f "/var/lib/systemd/linger/$USER" ]]; then
        report_pass "User lingering enabled" "$USER"
    else
        report_fail "User lingering not enabled" "Run: sudo loginctl enable-linger $USER"
    fi
fi

# Check rm_source from board API
BOARD_JSON=""
if BOARD_JSON="$(curl -fsS --max-time 5 "http://${HOST}:${PORT}/api/staff/board?local=1" 2>/dev/null)"; then
    RM_STATUS="$(printf '%s' "$BOARD_JSON" | grep -o '"rm_source":{[^}]*' || echo "")"
    if printf '%s' "$RM_STATUS" | grep -qi '"status":"ok"'; then
        report_pass "Board rm_source reports status ok"
    elif printf '%s' "$RM_STATUS" | grep -qi '"status"'; then
        report_pass "Board rm_source present" "$RM_STATUS"
    else
        report_fail "Board rm_source missing or errored" "$RM_STATUS"
    fi
else
    report_fail "Board API unreachable" "http://${HOST}:${PORT}/api/staff/board?local=1"
fi

# -----------------------------------------------------------------------------
# 6. Holds Check
# -----------------------------------------------------------------------------
printf "\n6. Holds Check\n"

SCHEDULE_JSON=""
if SCHEDULE_JSON="$(curl -fsS --max-time 5 "http://${HOST}:${PORT}/api/staff/schedule" 2>/dev/null)"; then
    # Look for any non-null or non-empty hold in schedule
    BLOCKED_HOLDS="$(printf '%s' "$SCHEDULE_JSON" | grep -o '"hold":"[^"]*' | grep -v '"hold":""' | cut -d'"' -f4 || echo "")"
    if [[ -z "$BLOCKED_HOLDS" ]]; then
        report_pass "Zero worker holds blocking schedule"
    else
        report_fail "Scheduled roles blocked by holds" "$BLOCKED_HOLDS"
    fi
else
    report_fail "Schedule API unreachable" "http://${HOST}:${PORT}/api/staff/schedule"
fi

# -----------------------------------------------------------------------------
# 7. Scheduler Expectation Check
# -----------------------------------------------------------------------------
printf "\n7. Scheduler Expectation (%s)\n" "$ROLE"

ENV_SCHEDULER=""
if [[ -f "$ENV_FILE" ]]; then
    ENV_SCHEDULER="$(grep '^STAFF_SCHEDULER_ENABLED=' "$ENV_FILE" | cut -d'=' -f2 | tr -d '"'"'" || echo "")"
fi

if [[ "$ENV_SCHEDULER" == "$EXPECTED_SCHEDULER" ]]; then
    report_pass "Env STAFF_SCHEDULER_ENABLED matches expectation" "value=${ENV_SCHEDULER}"
else
    report_fail "Env STAFF_SCHEDULER_ENABLED mismatch" "expected=${EXPECTED_SCHEDULER}, got=${ENV_SCHEDULER}"
fi

if [[ -n "$BOARD_JSON" ]]; then
    RUNNING_SCHEDULER="$(printf '%s' "$BOARD_JSON" | grep -o '"scheduler":[01]' | cut -d':' -f2 || echo "")"
    if [[ "$RUNNING_SCHEDULER" == "$EXPECTED_SCHEDULER" ]]; then
        report_pass "Live board scheduler state matches expectation" "value=${RUNNING_SCHEDULER}"
    else
        report_fail "Live board scheduler state mismatch" "expected=${EXPECTED_SCHEDULER}, got=${RUNNING_SCHEDULER}"
    fi
fi

# -----------------------------------------------------------------------------
# 8. Ollama Reachability Check
# -----------------------------------------------------------------------------
printf "\n8. Ollama Reachability\n"

if [[ "$SKIP_NETWORK" == "1" ]]; then
    report_pass "Ollama reachability check skipped" "--skip-network enabled"
else
    GATEWAY="$(ip route 2>/dev/null | awk '/default/{print $3; exit}' || echo "127.0.0.1")"
    OLLAMA_TARGETS=("127.0.0.1" "$GATEWAY")
    OLLAMA_SUCCESS=0
    OLLAMA_VER=""

    for target in "${OLLAMA_TARGETS[@]}"; do
        log_verbose "Testing Ollama at http://${target}:11434/api/version"
        if OLLAMA_RES="$(curl -fsS --max-time 4 "http://${target}:11434/api/version" 2>/dev/null)"; then
            OLLAMA_VER="$(printf '%s' "$OLLAMA_RES" | grep -o '"version":"[^"]*' | cut -d'"' -f4 || echo "detected")"
            report_pass "Ollama server reachable at ${target}:11434" "version=${OLLAMA_VER}"
            OLLAMA_SUCCESS=1
            break
        fi
    done

    if [[ "$OLLAMA_SUCCESS" != "1" ]]; then
        report_fail "Ollama server unreachable at 127.0.0.1 or gateway ${GATEWAY}:11434"
    fi
fi

# -----------------------------------------------------------------------------
# 9. Provider Availability & Ad-hoc Health Runs
# -----------------------------------------------------------------------------
printf "\n9. Provider Availability & Ad-hoc Verification\n"

REQUIRED_PROVIDERS=("claude" "codex" "antigravity" "cursor" "ollama" "claude-ollama")

if [[ -n "$BOARD_JSON" ]]; then
    for prov in "${REQUIRED_PROVIDERS[@]}"; do
        if printf '%s' "$BOARD_JSON" | grep -qi "\"${prov}\":true"; then
            report_pass "Provider available on board: ${prov}"
        else
            report_fail "Provider not marked available on board: ${prov}"
        fi
    done
fi

if [[ "$RUN_AD_HOC" == "1" ]]; then
    printf "\nRunning live ad-hoc runs for providers (--run-ad-hoc):\n"
    for prov in "${REQUIRED_PROVIDERS[@]}"; do
        # Mapping to actual dispatch provider names
        DISPATCH_PROV="$prov"
        if [[ "$prov" == "cursor" ]]; then
            DISPATCH_PROV="cursor-agent"
        fi

        log_verbose "Dispatching ad-hoc run for provider ${DISPATCH_PROV}..."
        PAYLOAD="$(printf '{"provider":"%s","machine":"local","prompt":"Health check: do not change anything. Reply OK, then STAFF_RESULT: ok"}' "$DISPATCH_PROV")"
        
        RUN_RESP=""
        if RUN_RESP="$(curl -fsS -X POST "http://${HOST}:${PORT}/api/staff/ad-hoc/run" \
            -H 'Content-Type: application/json' \
            -H 'X-Requested-With: XMLHttpRequest' \
            --data "$PAYLOAD" 2>/dev/null)"; then
            RUN_ID="$(printf '%s' "$RUN_RESP" | grep -o '"id":"[^"]*' | cut -d'"' -f4 || echo "")"
            if [[ -z "$RUN_ID" ]]; then
                report_fail "Ad-hoc run for ${DISPATCH_PROV}" "No run ID returned in response: $RUN_RESP"
                continue
            fi

            # Poll for completion (up to 45 seconds)
            STATUS=""
            for ((i=0; i<45; i++)); do
                sleep 1
                RUN_DETAIL="$(curl -fsS "http://${HOST}:${PORT}/api/staff/runs/${RUN_ID}" 2>/dev/null || echo "")"
                STATUS="$(printf '%s' "$RUN_DETAIL" | grep -o '"status":"[^"]*' | cut -d'"' -f4 || echo "")"
                if [[ "$STATUS" == "succeeded" || "$STATUS" == "failed" || "$STATUS" == "cancelled" ]]; then
                    break
                fi
            done

            if [[ "$STATUS" == "succeeded" ]]; then
                report_pass "Ad-hoc run succeeded: ${DISPATCH_PROV}" "run_id=${RUN_ID}"
            else
                report_fail "Ad-hoc run failed: ${DISPATCH_PROV}" "status=${STATUS}, run_id=${RUN_ID}"
            fi
        else
            report_fail "Ad-hoc dispatch failed for ${DISPATCH_PROV}"
        fi
    done
fi

printf "\n=== Summary: %d passed, %d failed (total: %d) ===\n" "$PASS_COUNT" "$FAIL_COUNT" "$TOTAL_COUNT"

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    exit 1
fi
exit 0
