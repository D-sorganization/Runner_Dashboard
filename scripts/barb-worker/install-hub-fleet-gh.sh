#!/usr/bin/env bash
# install-hub-fleet-gh.sh — hub-minted gh/git for barb-worker lanes (#3657); called from bootstrap.sh (#3659).
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RD_ROOT="${RUNNER_DASHBOARD_ROOT:-${HOME}/src/Runner_Dashboard}"
HUB_TOKEN_PY="${RD_ROOT}/scripts/barb_github_hub_token.py"
REAL_GH="${STAFF_REAL_GH:-$(command -v gh || true)}"
BIN_DIR="${HOME}/.local/bin"
LIB_DIR="${HOME}/.local/lib/hub-fleet-gh"
DRY_RUN=0

die() { echo "install-hub-fleet-gh: $*" >&2; exit 2; }
say() { echo "install-hub-fleet-gh: $*"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,3p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[[ -f "${HUB_TOKEN_PY}" ]] || die "missing ${HUB_TOKEN_PY} (clone Runner_Dashboard first)"
[[ -n "${REAL_GH}" && -x "${REAL_GH}" ]] || die "real gh not found (set STAFF_REAL_GH)"

run() { if [[ "${DRY_RUN}" == "1" ]]; then say "[dry-run] $*"; else eval "$@"; fi; }

FLEET_ENV_SNIPPET='[[ -f "${HOME}/.profile" ]] && source "${HOME}/.profile"
_t="${HOME}/.config/runner-dashboard/token"
if [[ -z "${FLEET_API_TOKEN:-}" && -r "${_t}" ]]; then IFS= read -r FLEET_API_TOKEN <"${_t}" || FLEET_API_TOKEN=""; export FLEET_API_TOKEN; fi
unset _t'

run "mkdir -p '${BIN_DIR}' '${LIB_DIR}'"

if [[ "${DRY_RUN}" == "1" ]]; then
    say "[dry-run] write gh and git-credential helpers under ${LIB_DIR}"
else
    cat >"${LIB_DIR}/gh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
${FLEET_ENV_SNIPPET}
unset GH_TOKEN GITHUB_TOKEN
REAL_GH="${REAL_GH}"
if ! TOKEN="\$(python3 "${HUB_TOKEN_PY}" token 2>/dev/null)"; then
  echo "hub fleet gh: App token mint failed; refusing personal auth fallback" >&2
  exit 3
fi
export GH_TOKEN="\${TOKEN}" GITHUB_TOKEN="\${TOKEN}"
exec "\${REAL_GH}" "\$@"
EOF
    cat >"${LIB_DIR}/git-credential-hub-fleet-gh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
${FLEET_ENV_SNIPPET}
exec python3 "${HUB_TOKEN_PY}" "\$@"
EOF
    chmod 755 "${LIB_DIR}/gh" "${LIB_DIR}/git-credential-hub-fleet-gh"
    ln -sf "${LIB_DIR}/gh" "${BIN_DIR}/gh"
    ln -sf "${LIB_DIR}/git-credential-hub-fleet-gh" "${BIN_DIR}/git-credential-hub-fleet-gh"
    touch "${HOME}/.profile"
    grep -q 'hub-fleet-gh PATH' "${HOME}/.profile" 2>/dev/null || cat >>"${HOME}/.profile" <<EOF

# hub-fleet-gh PATH (install-hub-fleet-gh.sh)
export STAFF_REAL_GH="${REAL_GH}"
export RUNNER_DASHBOARD_ROOT="${RD_ROOT}"
export PATH="${BIN_DIR}:\${PATH}"
EOF
    git config --global --unset-all credential.https://github.com.helper || true
    git config --global credential.https://github.com.helper "${BIN_DIR}/git-credential-hub-fleet-gh" || true
fi

say "installed hub-fleet-gh (STAFF_REAL_GH=${REAL_GH})"
