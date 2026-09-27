#!/usr/bin/env bash
# ==============================================================================
# reap-wsl-leaked-chrome.sh
#
# Fleet safety net for issue #1678: self-hosted Linux runners inside WSL can
# launch Windows Chrome through /mnt/c interop (e.g. lhci/chrome-launcher).
# Those processes are parented to the WSL interop host rather than to the
# runner job, so normal job cleanup never kills them.
#
# This script uses PowerShell interop (Win32_Process via Get-CimInstance) to
# stop Windows chrome.exe processes that BOTH:
#   - have a CommandLine matching a lighthouse/chrome-launcher temp profile
#     (\AppData\Local\lighthouse.<n>), and
#   - are older than LEAKED_CHROME_MAX_AGE_HOURS (default 2h), so running CI
#     jobs are left untouched.
#
# The user's own Chrome never matches: it has no lighthouse temp profile.
# Profile directories are never deleted by this script.
#
# Honours DRY_RUN=1 (print the count only, do not stop anything).
# Never fails: if powershell.exe is unavailable, or interop fails/times out,
# print a warning and exit 0.
# ==============================================================================

set -uo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info() { echo -e "${CYAN}[INFO]${NC} $*"; }
ok() { echo -e "${GREEN}[ OK ]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }

DRY_RUN="${DRY_RUN:-0}"
MAX_AGE_HOURS="${LEAKED_CHROME_MAX_AGE_HOURS:-2}"
POWERSHELL_BIN="${POWERSHELL_BIN:-powershell.exe}"

if ! [[ "${MAX_AGE_HOURS}" =~ ^[0-9]+$ ]]; then
    warn "Invalid LEAKED_CHROME_MAX_AGE_HOURS=${MAX_AGE_HOURS}; using default of 2"
    MAX_AGE_HOURS=2
fi

PS_BIN="${POWERSHELL_BIN}"
if ! command -v "${PS_BIN}" >/dev/null 2>&1; then
    warn "powershell.exe not found on PATH (POWERSHELL_BIN=${POWERSHELL_BIN}); skipping leaked-Chrome reap"
    exit 0
fi

# PowerShell 5.1 compatible: no ?. / ??, CreationDate compared as [datetime].
# Dry-run and real-run use distinct snippets so a dry run's PowerShell text
# never contains a Stop-Process call, even inert or unreachable.
PS_FILTER=$(cat <<EOF
\$maxAgeHours = ${MAX_AGE_HOURS}
\$cutoff = (Get-Date).AddHours(-\$maxAgeHours)
\$procs = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Where-Object {
    \$_.CommandLine -like '*\\AppData\\Local\\lighthouse.*' -and [datetime]\$_.CreationDate -lt \$cutoff
}
\$count = 0
if (\$procs) { \$count = @(\$procs).Count }
EOF
)

if [[ "${DRY_RUN}" == "1" ]]; then
    PS_SNIPPET="${PS_FILTER}
Write-Output \"DRY RUN: would stop \$count leaked lighthouse chrome.exe process(es)\""
else
    PS_SNIPPET="${PS_FILTER}
foreach (\$p in \$procs) {
    Stop-Process -Id \$p.ProcessId -Force -ErrorAction SilentlyContinue
}
Write-Output \"Stopped \$count leaked lighthouse chrome.exe process(es)\""
fi

info "Reaping WSL-leaked Windows Chrome (lighthouse profiles older than ${MAX_AGE_HOURS}h, dry_run=${DRY_RUN})"

OUTPUT=""
if command -v timeout >/dev/null 2>&1; then
    OUTPUT="$(timeout 120 "${PS_BIN}" -NoProfile -NonInteractive -Command "${PS_SNIPPET}" 2>&1)"
    STATUS=$?
else
    OUTPUT="$("${PS_BIN}" -NoProfile -NonInteractive -Command "${PS_SNIPPET}" 2>&1)"
    STATUS=$?
fi

if [[ ${STATUS} -ne 0 ]]; then
    warn "powershell.exe interop failed or timed out (exit ${STATUS}); skipping leaked-Chrome reap"
    exit 0
fi

ok "${OUTPUT}"
exit 0
