#!/usr/bin/env bash
# install-fleet-gh.sh — install fail-closed fleet GitHub App gh/git wrappers (RD fleet-gh).
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${SCRIPT_DIR}/fleet-gh"
LIB_DIR="${FLEET_GH_LIB:-/usr/local/lib/fleet-gh}"
BIN_DIR="${FLEET_GH_BIN:-/usr/local/bin}"
DROPIN_DIR="/etc/systemd/system/runner-dashboard.service.d"
DROPIN_FILE="${DROPIN_DIR}/fleet-gh.conf"
PROFILE_SNIPPET="/etc/profile.d/fleet-gh.sh"
REAL_GH="${STAFF_REAL_GH:-/usr/bin/gh}"
DEPLOY_DIR="${RUNNER_DASHBOARD_DEPLOY_DIR:-${HOME}/actions-runners/dashboard}"
DRY_RUN=0

die() { echo "install-fleet-gh: $*" >&2; exit 2; }
say() { echo "install-fleet-gh: $*"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help)
            sed -n '2,12p' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *) die "unknown argument: $1" ;;
    esac
done

[[ -f "${SOURCE_DIR}/gh_app_credential.py" ]] || die "missing source ${SOURCE_DIR}/gh_app_credential.py"
command -v "${REAL_GH}" >/dev/null 2>&1 || die "real gh not found at ${REAL_GH}"

run() {
    if [[ "${DRY_RUN}" == "1" ]]; then
        say "[dry-run] $*"
    else
        eval "$@"
    fi
}

install -d -m 755 "${LIB_DIR}"
install -m 755 "${SOURCE_DIR}/gh_app_credential.py" "${LIB_DIR}/"
install -m 755 "${SOURCE_DIR}/gh" "${LIB_DIR}/gh"
install -m 755 "${SOURCE_DIR}/git-credential-fleet-gh" "${LIB_DIR}/"

run "install -m 755 '${SOURCE_DIR}/gh' '${BIN_DIR}/gh'"
run "install -m 755 '${SOURCE_DIR}/gh-personal' '${BIN_DIR}/gh-personal'"
run "install -m 755 '${SOURCE_DIR}/git-credential-fleet-gh' '${BIN_DIR}/git-credential-fleet-gh'"

if [[ "${DRY_RUN}" == "1" ]]; then
    say "[dry-run] write ${DROPIN_FILE} and ${PROFILE_SNIPPET}"
else
    install -d -m 755 "${DROPIN_DIR}"
    cat >"${DROPIN_FILE}" <<EOF
[Service]
Environment=STAFF_REAL_GH=${REAL_GH}
Environment=FLEET_GH_BACKEND_ROOT=${DEPLOY_DIR}/backend
Environment=FLEET_GH_LIB=${LIB_DIR}
Environment=PATH=${BIN_DIR}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
EOF
    cat >"${PROFILE_SNIPPET}" <<EOF
# fleet-gh: App-token gh wrapper ahead of system gh
export STAFF_REAL_GH="${REAL_GH}"
export FLEET_GH_BACKEND_ROOT="${DEPLOY_DIR}/backend"
export FLEET_GH_LIB="${LIB_DIR}"
export PATH="${BIN_DIR}:\${PATH}"
EOF
    chmod 644 "${PROFILE_SNIPPET}"
fi

if [[ "${DRY_RUN}" != "1" ]]; then
    git config --global credential.https://github.com.helper "${BIN_DIR}/git-credential-fleet-gh" || true
fi

say "installed fleet-gh (STAFF_REAL_GH=${REAL_GH}, lib=${LIB_DIR})"
say "reload systemd and open a new shell; run: gh auth status (should show the fleet App bot)"
