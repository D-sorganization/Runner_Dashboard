#!/usr/bin/env bash
# One-time, operator-controlled bootstrap for issue #1138.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNNER_USER="dieterolson"
EXPECTED_COMMIT=""
COSIGN_SOURCE=""
COSIGN_SHA256=""
STATE_ROOT="/var/lib/runner-dashboard-qualified-deploy"
HELPER_DEST="/usr/local/sbin/runner-dashboard-qualified-deploy"
LIB_DEST="/usr/local/lib/runner-dashboard/qualified-release-lib.sh"
RUNTIME_LIB_DEST="/usr/local/lib/runner-dashboard/qualified-release-runtime-lib.sh"
SCHEDULE_DEST="/usr/local/share/runner-dashboard/runner-schedule-oglaptop.json"
SUDOERS_DEST="/etc/sudoers.d/runner-dashboard-qualified-deploy"

usage() {
    cat <<'EOF'
Usage: sudo bash deploy/bootstrap-qualified-release-deploy.sh [options]

Options:
  --expected-commit SHA   Exact reviewed protected-main commit (required)
  --cosign-source PATH     Install a reviewed cosign binary at /usr/local/bin/cosign
  --cosign-sha256 SHA256   Required checksum for --cosign-source
  --project-root PATH      Protected-main checkout root (default: inferred)
  -h, --help               Show this help

The runner account and host paths are intentionally fixed for OGLaptop.
EOF
}

fail() {
    printf '[FAIL] %s\n' "$*" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --expected-commit) EXPECTED_COMMIT="$2"; shift 2 ;;
        --cosign-source) COSIGN_SOURCE="$2"; shift 2 ;;
        --cosign-sha256) COSIGN_SHA256="$2"; shift 2 ;;
        --project-root) PROJECT_ROOT="$(realpath "$2")"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) fail "unknown option: $1" ;;
    esac
done

[[ "${EUID}" -eq 0 ]] || fail "bootstrap must run as root"
[[ "${EXPECTED_COMMIT}" =~ ^[0-9a-f]{40}$ ]] || fail "--expected-commit is required"
id "${RUNNER_USER}" > /dev/null 2>&1 || fail "required runner user is absent"
for command in git install visudo sha256sum stat realpath; do
    command -v "${command}" > /dev/null 2>&1 || fail "required command is absent: ${command}"
done

HELPER_SOURCE="${PROJECT_ROOT}/deploy/qualified-release-deploy.sh"
LIB_SOURCE="${PROJECT_ROOT}/deploy/qualified-release-lib.sh"
RUNTIME_LIB_SOURCE="${PROJECT_ROOT}/deploy/qualified-release-runtime-lib.sh"
SCHEDULE_SOURCE="${PROJECT_ROOT}/config/runner-schedule-oglaptop.json"
for source in "${HELPER_SOURCE}" "${LIB_SOURCE}" "${RUNTIME_LIB_SOURCE}" "${SCHEDULE_SOURCE}"; do
    [[ -f "${source}" && ! -L "${source}" ]] || fail "bootstrap source is absent or unsafe"
done
ACTUAL_COMMIT="$(git -c "safe.directory=${PROJECT_ROOT}" -C "${PROJECT_ROOT}" rev-parse HEAD)"
[[ "${ACTUAL_COMMIT}" == "${EXPECTED_COMMIT}" ]] || fail "checkout does not match the reviewed commit"
[[ -z "$(git -c "safe.directory=${PROJECT_ROOT}" -C "${PROJECT_ROOT}" status --porcelain)" ]] \
    || fail "bootstrap checkout is not clean"
for source in "${HELPER_SOURCE}" "${LIB_SOURCE}" "${RUNTIME_LIB_SOURCE}" "${SCHEDULE_SOURCE}"; do
    git -c "safe.directory=${PROJECT_ROOT}" -C "${PROJECT_ROOT}" \
        ls-files --error-unmatch "${source#"${PROJECT_ROOT}/"}" > /dev/null \
        || fail "bootstrap source is not tracked by the reviewed commit"
done

if [[ -n "${COSIGN_SOURCE}" || -n "${COSIGN_SHA256}" ]]; then
    [[ -n "${COSIGN_SOURCE}" && "${COSIGN_SHA256}" =~ ^[0-9a-f]{64}$ ]] \
        || fail "cosign source and lowercase SHA-256 must be supplied together"
    [[ -f "${COSIGN_SOURCE}" && ! -L "${COSIGN_SOURCE}" ]] || fail "cosign source is unsafe"
    [[ "$(sha256sum "${COSIGN_SOURCE}" | awk '{print $1}')" == "${COSIGN_SHA256}" ]] \
        || fail "cosign source checksum mismatch"
    install -o root -g root -m 0755 "${COSIGN_SOURCE}" /usr/local/bin/cosign
fi

[[ -x /usr/local/bin/cosign ]] || fail "root-owned cosign is required"
[[ "$(stat -c '%U:%G' /usr/local/bin/cosign)" == "root:root" ]] \
    || fail "cosign must be owned by root:root"
[[ $((8#$(stat -c '%a' /usr/local/bin/cosign) & 8#022)) -eq 0 ]] \
    || fail "cosign must not be group/other writable"

install -d -o root -g root -m 0755 /usr/local/lib/runner-dashboard
install -d -o root -g root -m 0755 /usr/local/share/runner-dashboard
install -d -o root -g root -m 0700 "${STATE_ROOT}" "${STATE_ROOT}/transactions"
install -d -o "${RUNNER_USER}" -g "${RUNNER_USER}" -m 0700 "${STATE_ROOT}/inbox"
install -o root -g root -m 0755 "${HELPER_SOURCE}" "${HELPER_DEST}"
install -o root -g root -m 0644 "${LIB_SOURCE}" "${LIB_DEST}"
install -o root -g root -m 0644 "${RUNTIME_LIB_SOURCE}" "${RUNTIME_LIB_DEST}"
install -o root -g root -m 0644 "${SCHEDULE_SOURCE}" "${SCHEDULE_DEST}"

SUDOERS_TEMP="$(mktemp)"
trap 'rm -f "${SUDOERS_TEMP}"' EXIT
cat > "${SUDOERS_TEMP}" <<EOF
# Exact no-argument transaction entrypoint. Request data arrives on stdin and
# is validated against a closed schema by the root-owned helper.
${RUNNER_USER} ALL=(root) NOPASSWD: ${HELPER_DEST}
EOF
chmod 0440 "${SUDOERS_TEMP}"
visudo -cf "${SUDOERS_TEMP}" > /dev/null || fail "sudoers validation failed"
install -o root -g root -m 0440 "${SUDOERS_TEMP}" "${SUDOERS_DEST}"
visudo -cf "${SUDOERS_DEST}" > /dev/null || fail "installed sudoers validation failed"

for installed in "${HELPER_DEST}" "${LIB_DEST}" "${RUNTIME_LIB_DEST}" "${SCHEDULE_DEST}" "${SUDOERS_DEST}"; do
    [[ "$(stat -c '%U:%G' "${installed}")" == "root:root" ]] \
        || fail "installed authority is not root:root"
done

printf '[OK] qualified OGLaptop deployment authority installed; no service was changed\n'
