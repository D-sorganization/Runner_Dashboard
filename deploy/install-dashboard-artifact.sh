#!/usr/bin/env bash
# ==============================================================================
# install-dashboard-artifact.sh — Install a versioned runner-dashboard artifact.
# ==============================================================================
# This script verifies the published tarball checksum, validates the packaged
# file inventory, stages the artifact into the deployed dashboard directory, and
# writes deployment metadata that preserves the artifact's build identity.
#
# Fail-closed ordering (#1212): the wheelhouse ABI check, interpreter selection
# and a complete offline dependency install into a throwaway venv all run
# BEFORE the deploy directory is touched. Only then is the live `.venv` swapped
# (the previous one is restored if the rebuild fails) and the code synced.
#
# Usage:
#   bash deploy/install-dashboard-artifact.sh --artifact /path/to/dashboard-4.0.1.tar.gz
#   bash deploy/install-dashboard-artifact.sh --artifact https://.../dashboard-4.0.1.tar.gz
# ==============================================================================

set -euo pipefail

# shellcheck source=deploy/python-runtime.sh
source "$(dirname "${BASH_SOURCE[0]}")/python-runtime.sh"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

info() { echo -e "${CYAN}[INFO]${NC} $*"; }
ok() { echo -e "${GREEN}[ OK ]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

ARTIFACT_SOURCE=""
CHECKSUM_INPUT=""
DEPLOY_DIR="${DEPLOY_DIR:-$HOME/actions-runners/dashboard}"
INSTALLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_IMPORT_CHECK='import fastapi, httpx, psutil, uvicorn, yaml'

usage() {
    cat <<'EOF'
Usage:
  install-dashboard-artifact.sh --artifact PATH_OR_URL [--checksum SHA256] [--deploy-dir PATH]

Options:
  --artifact PATH_OR_URL   Dashboard tarball path or release URL
  --checksum SHA256        Expected SHA-256 checksum (optional if .sha256 sidecar exists)
  --deploy-dir PATH        Deployed dashboard directory (default: ~/actions-runners/dashboard)
  -h, --help               Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --artifact) ARTIFACT_SOURCE="$2"; shift 2 ;;
        --checksum) CHECKSUM_INPUT="$2"; shift 2 ;;
        --deploy-dir) DEPLOY_DIR="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) fail "Unknown option: $1" ;;
    esac
done

[[ -n "${ARTIFACT_SOURCE}" ]] || fail "Missing --artifact PATH_OR_URL"

tmpdir="$(mktemp -d)"
live_venv=""
previous_venv=""
VENV_SWAP_ACTIVE=0
cleanup() {
    local status=$?
    if [[ "${VENV_SWAP_ACTIVE}" == "1" ]]; then
        echo -e "${YELLOW}[WARN]${NC} Install failed; restoring the previous runtime venv" >&2
        rm -rf "${live_venv}"
        [[ -d "${previous_venv}" ]] && mv "${previous_venv}" "${live_venv}"
    fi
    rm -rf "${tmpdir}"
    exit "${status}"
}
trap cleanup EXIT

artifact_name="$(basename "${ARTIFACT_SOURCE%%\?*}")"
artifact_path="${tmpdir}/${artifact_name}"
checksum_path="${artifact_path}.sha256"

fetch_artifact() {
    local source="$1"
    if [[ "$source" =~ ^https?:// ]]; then
        info "Downloading artifact from ${source}"
        curl -fsSL "$source" -o "$artifact_path"
        if [[ -n "${CHECKSUM_INPUT}" ]]; then
            echo "${CHECKSUM_INPUT}  ${artifact_name}" > "$checksum_path"
        else
            curl -fsSL "${source}.sha256" -o "$checksum_path" || fail "Failed to download checksum from ${source}.sha256"
        fi
    else
        [[ -f "$source" ]] || fail "Artifact not found: $source"
        cp "$source" "$artifact_path"
        if [[ -n "${CHECKSUM_INPUT}" ]]; then
            echo "${CHECKSUM_INPUT}  ${artifact_name}" > "$checksum_path"
        elif [[ -f "${source}.sha256" ]]; then
            cp "${source}.sha256" "$checksum_path"
        else
            fail "Missing checksum file: ${source}.sha256 (or pass --checksum <sha256>)"
        fi
    fi
}

validate_artifact_layout() {
    local stage_dir="$1"
    for required in VERSION deployment.json FILES.txt backend frontend deploy local_apps.json refresh-token.sh \
        requirements.lock.txt backend/wheels wsl-mirrored-port-helper.sh; do
        [[ -e "${stage_dir}/${required}" ]] || fail "Artifact is missing required path: ${required}"
    done
    python3 - "$stage_dir/deployment.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for key in ("version", "git_sha", "build_timestamp", "compatibility"):
    if key not in payload:
        raise SystemExit(f"deployment.json missing required key: {key}")
compat = payload["compatibility"]
if not isinstance(compat, dict):
    raise SystemExit("deployment.json compatibility block must be an object")
for key in ("python_requires", "python_minor", "service_name", "artifact_schema"):
    if key not in compat:
        raise SystemExit(f"compatibility missing required key: {key}")
if compat["artifact_schema"] != "runner-dashboard-artifact-v2":
    raise SystemExit("unsupported artifact schema; expected runner-dashboard-artifact-v2")
PY
}

apply_deployment_metadata() {
    local stage_dir="$1"
    local source="$2"
    python3 - "$stage_dir/deployment.json" "$source" "$DEPLOY_DIR/deployment.json" <<'PY'
import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

source_path = Path(sys.argv[1])
artifact_source = sys.argv[2]
output_path = Path(sys.argv[3])
payload = json.loads(source_path.read_text(encoding="utf-8"))
payload["deployed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
payload["deployed_from"] = artifact_source
payload["hostname"] = socket.gethostname()
payload.setdefault("source", "github-actions-artifact-build")
payload.setdefault("app", "runner-dashboard")
output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

read_artifact_python_minor() {
    python3 -c '
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["compatibility"]["python_minor"])
' "$1"
}

# build_runtime_venv PYTHON VENV_DIR STAGE_DIR — venv + offline hashed install + pip check + import smoke.
# Uses the venv's own pip (bootstrapped by ensurepip), never host pip (#1212).
build_runtime_venv() {
    local python="$1"
    local venv_dir="$2"
    local source_dir="$3"
    "${python}" -m venv "${venv_dir}" || return 1
    "${venv_dir}/bin/python" -m pip install \
        --disable-pip-version-check \
        --no-index \
        --require-hashes \
        --find-links="${source_dir}/backend/wheels" \
        -r "${source_dir}/requirements.lock.txt" || return 1
    "${venv_dir}/bin/python" -m pip check || return 1
    "${venv_dir}/bin/python" -c "${RUNTIME_IMPORT_CHECK}" || return 1
}

fetch_artifact "${ARTIFACT_SOURCE}"

info "Verifying checksum for ${artifact_name}"
(cd "${tmpdir}" && sha256sum -c "$(basename "${checksum_path}")")

stage_dir="${tmpdir}/stage"
mkdir -p "${stage_dir}"
tar -xzf "${artifact_path}" -C "${stage_dir}"

validate_artifact_layout "${stage_dir}"

info "Validating packaged file inventory"
while IFS= read -r file_path; do
    [[ -z "${file_path}" ]] && continue
    [[ -e "${stage_dir}/${file_path}" ]] || fail "FILES.txt references missing path: ${file_path}"
done < "${stage_dir}/FILES.txt"

# ── Preflight: everything below must pass before the deploy dir is touched ──
ARTIFACT_PYTHON_MINOR="$(read_artifact_python_minor "${stage_dir}/deployment.json")"

info "Checking wheelhouse ABI against declared Python ${ARTIFACT_PYTHON_MINOR}"
[[ -f "${INSTALLER_DIR}/check-wheelhouse-abi.py" ]] || fail "check-wheelhouse-abi.py missing next to installer"
python3 "${INSTALLER_DIR}/check-wheelhouse-abi.py" \
    --python-minor "${ARTIFACT_PYTHON_MINOR}" \
    --wheel-dir "${stage_dir}/backend/wheels" \
    || fail "Artifact wheelhouse does not match its declared Python ${ARTIFACT_PYTHON_MINOR}; deploy dir untouched"

RUNTIME_PYTHON="$(select_dashboard_python "${ARTIFACT_PYTHON_MINOR}" venv)" \
    || fail "Python ${ARTIFACT_PYTHON_MINOR} with venv support is required by this artifact; deploy dir untouched"
info "Using ${RUNTIME_PYTHON} for the runtime venv"

info "Preflight: installing backend dependencies offline into a staging venv..."
build_runtime_venv "${RUNTIME_PYTHON}" "${tmpdir}/preflight-venv" "${stage_dir}" \
    || fail "Offline dependency preflight failed; deploy dir untouched"
ok "Preflight passed"

# ── Mutation: swap the runtime venv first (restorable), then sync code ──
mkdir -p "${DEPLOY_DIR}"
command -v rsync >/dev/null 2>&1 || fail "rsync is required for state-preserving artifact installation"
live_venv="${DEPLOY_DIR}/.venv"
previous_venv="${DEPLOY_DIR}/.venv.previous-install"
if [[ -d "${previous_venv}" && ! -e "${live_venv}" ]]; then
    warn "Recovering ${previous_venv} left by an interrupted install"
    mv "${previous_venv}" "${live_venv}"
fi
rm -rf "${previous_venv}"
[[ -e "${live_venv}" ]] && mv "${live_venv}" "${previous_venv}"
VENV_SWAP_ACTIVE=1

info "Installing backend dependencies offline into ${live_venv}..."
build_runtime_venv "${RUNTIME_PYTHON}" "${live_venv}" "${stage_dir}" \
    || fail "Runtime venv rebuild failed after a passing preflight"

info "Installing artifact into ${DEPLOY_DIR}"
rsync -a --delete \
    --exclude='/.venv' \
    --exclude='/.venv.previous-install' \
    --exclude='.env' \
    --exclude='.*_state.json' \
    --exclude='*_history.json' \
    --exclude='*.db' \
    --exclude='*.db-*' \
    "${stage_dir}/" "${DEPLOY_DIR}/"

apply_deployment_metadata "${stage_dir}" "${ARTIFACT_SOURCE}"
chmod +x "${DEPLOY_DIR}/refresh-token.sh"
chmod +x "${DEPLOY_DIR}/wsl-mirrored-port-helper.sh"
if [[ -d "${DEPLOY_DIR}/deploy" ]]; then
    find "${DEPLOY_DIR}/deploy" -maxdepth 1 -type f -name '*.sh' -exec chmod +x {} +
fi
find "${DEPLOY_DIR}/backend" -maxdepth 2 -type f \
    \( -name '*.yml' -o -name '*.yaml' -o -name '*.json' \) \
    -exec chmod 0644 {} + 2>/dev/null || true
chmod 644 "${DEPLOY_DIR}/deployment.json"

"${DEPLOY_DIR}/.venv/bin/python" -c "${RUNTIME_IMPORT_CHECK}"
VENV_SWAP_ACTIVE=0
rm -rf "${previous_venv}"

ok "Dashboard artifact installed to ${DEPLOY_DIR}"
