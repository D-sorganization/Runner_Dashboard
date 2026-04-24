#!/usr/bin/env bash
# ==============================================================================
# wsl-disk-management.sh (WSL dual-partition monitoring)
#
# Monitors BOTH WSL filesystem and Windows host disk:
# - WSL VHDX on /dev/sdd (the actual Linux filesystem)
# - Windows host disk at /mnt/c (where VHDX file lives and disk space is critical)
# ==============================================================================

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

# Config
DASHBOARD_DIR="${DASHBOARD_DIR:-$HOME/actions-runners/dashboard}"
# Resolve repo root relative to this script so it works in source tree and deployed copy.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
LOG_DIR="${LOG_DIR:-$HOME/.dashboard-logs}"
RUNNER_CLEANUP_SCRIPT="${RUNNER_CLEANUP_SCRIPT:-$SCRIPT_DIR/runner-cleanup.sh}"

# Thresholds
WSL_WARNING=80
WSL_CRITICAL=90
WINDOWS_WARNING=85    # Critical for VHDX location
WINDOWS_CRITICAL=95
MIN_FREE_GB=30

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/disk-management-$(date +%Y-%m-%d).log"

log_info() { echo -e "${CYAN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} ${GREEN}[INFO]${NC} $*" | tee -a "$LOG_FILE"; }
log_warn() { echo -e "${CYAN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} ${YELLOW}[WARN]${NC} $*" | tee -a "$LOG_FILE"; }
log_error() { echo -e "${CYAN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} ${RED}[ERROR]${NC} $*" | tee -a "$LOG_FILE"; }

get_disk_info() {
    local mount="$1"
    df "$mount" 2>/dev/null | awk 'NR==2 {
        total=$2
        used=$3
        avail=$4
        pct=int(used*100/total)
        free_gb=int(avail/1024/1024)
        used_gb=int(used/1024/1024)
        total_gb=int(total/1024/1024)
        printf "%d|%d|%d|%d", pct, free_gb, used_gb, total_gb
    }'
}

check_disk() {
    local mount="$1"
    local name="$2"
    local warn_thresh="$3"
    local crit_thresh="$4"

    local info=$(get_disk_info "$mount")
    local pct=$(echo "$info" | cut -d'|' -f1)
    local free=$(echo "$info" | cut -d'|' -f2)
    local used=$(echo "$info" | cut -d'|' -f3)
    local total=$(echo "$info" | cut -d'|' -f4)

    log_info "$name: ${used}GB / ${total}GB (${pct}% used, ${free}GB free)"

    if [[ $pct -ge $crit_thresh ]]; then
        log_error "CRITICAL: $name at ${pct}% (threshold: ${crit_thresh}%)"
        return 2
    elif [[ $pct -ge $warn_thresh ]]; then
        log_warn "WARNING: $name at ${pct}% (threshold: ${warn_thresh}%)"
        return 1
    else
        return 0
    fi
}

run_cleanup() {
    log_warn "Running cleanup..."
    if [[ -f "$RUNNER_CLEANUP_SCRIPT" ]]; then
        chmod +x "$RUNNER_CLEANUP_SCRIPT"
        bash "$RUNNER_CLEANUP_SCRIPT" >> "$LOG_FILE" 2>&1 || log_error "Cleanup failed"
    fi
}

suggest_windows_actions() {
    log_error "Windows C: disk is at critical capacity!"
    log_error "The WSL VHDX file lives on C:. This is a host-side issue."
    echo "  Actions:" | tee -a "$LOG_FILE"
    echo "    1. Free up space on Windows C: drive (move files, uninstall programs, empty Recycle Bin)" | tee -a "$LOG_FILE"
    echo "    2. Check: du -sh /home /tmp /var/cache | sort -hr" | tee -a "$LOG_FILE"
    echo "    3. After freeing 50GB+, compact VHDX from Windows PowerShell (Admin):" | tee -a "$LOG_FILE"
    echo "       wsl --shutdown" | tee -a "$LOG_FILE"
    echo "       diskpart < EOF" | tee -a "$LOG_FILE"
    echo "       open file=\"%LOCALAPPDATA%\\Packages\\CanonicalGroupLimited.UbuntuWSL_79rhkp1fndgsc\\LocalState\\ext4.vhdx\"" | tee -a "$LOG_FILE"
    echo "       compact vdisk" | tee -a "$LOG_FILE"
    echo "       exit" | tee -a "$LOG_FILE"
    echo "       EOF" | tee -a "$LOG_FILE"
}

main() {
    log_info "========== Disk Management Check =========="

    # Dashboard health
    if curl -s http://localhost:8321/api/health > /dev/null 2>&1; then
        log_info "Dashboard: OK"
    else
        log_warn "Dashboard: Not responding"
    fi

    log_info ""

    # Check both filesystems
    local wsl_ret=0
    local windows_ret=0

    check_disk "/" "WSL Filesystem" "$WSL_WARNING" "$WSL_CRITICAL" || wsl_ret=$?
    log_info ""
    check_disk "/mnt/c" "Windows C: (VHDX location)" "$WINDOWS_WARNING" "$WINDOWS_CRITICAL" || windows_ret=$?

    log_info ""

    # Handle results
    if [[ $windows_ret -eq 2 ]]; then
        suggest_windows_actions
        run_cleanup || true
    elif [[ $windows_ret -eq 1 ]]; then
        log_warn "Windows disk approaching critical. Freeing space recommended."
        run_cleanup || true
    fi

    if [[ $wsl_ret -eq 2 ]]; then
        log_error "WSL filesystem critical!"
        run_cleanup || true
    elif [[ $wsl_ret -eq 1 ]]; then
        log_warn "WSL filesystem at warning level"
        run_cleanup || true
    fi

    [[ $wsl_ret -eq 0 && $windows_ret -eq 0 ]] && log_info "All systems healthy"
    log_info "========== Check Complete =========="
}

main
