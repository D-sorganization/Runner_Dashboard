#!/usr/bin/env bash
# ==============================================================================
# install-dashboard-artifact.sh — Install a versioned runner-dashboard artifact.
# ==============================================================================
# This script verifies the published tarball checksum, validates the packaged
# file inventory, stages the artifact into the deployed dashboard directory, and
# writes deployment metadata that preserves the artifact's build identity.
#
# Usage:
#   bash deploy/install-dashboard-artifact.sh --artifact /path/to/dashboard-4.0.1.tar.gz
#   bash deploy/install-dashboard-artifact.sh --artifact https://.../dashboard-4.0.1.tar.gz
# ==============================================================================

set -euo pipefail

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
DEPLOY_DIR="${DEPLOY_DIR:-$HOME/actions-runners/dashboard}"

usage() {
    cat <<'EOF'
Usage:
  install-dashboard-artifact.sh --artifact PATH_OR_URL [--deploy-dir PATH]

Options:
  --artifact PATH_OR_URL   Dashboard tarball path or release URL
  --deploy-dir PATH        Deployed dashboard directory (default: ~/actions-runners/dashboard)
  -h, --help               Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --artifact) ARTIFACT_SOURCE="$2"; shift 2 ;;
        --deploy-dir) DEPLOY_DIR="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) fail "Unknown option: $1" ;;
    esac
done

[[ -n "${ARTIFACT_SOURCE}" ]] || fail "Missing --artifact PATH_OR_URL"

tmpdir="$(mktemp -d)"
cleanup() {
    rm -rf "${tmpdir}"
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
        curl -fsSL "${source}.sha256" -o "$checksum_path"
    else
        [[ -f "$source" ]] || fail "Artifact not found: $source"
        cp "$source" "$artifact_path"
        if [[ -f "${source}.sha256" ]]; then
            cp "${source}.sha256" "$checksum_path"
        else
            fail "Missing checksum file: ${source}.sha256"
        fi
    fi
}

validate_artifact_layout() {
    local stage_dir="$1"
    for required in VERSION deployment.json FILES.txt backend frontend deploy local_apps.json refresh-token.sh; do
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
for key in ("python_requires", "service_name", "artifact_schema"):
    if key not in compat:
        raise SystemExit(f"compatibility missing required key: {key}")
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

info "Installing artifact into ${DEPLOY_DIR}"
mkdir -p "${DEPLOY_DIR}"
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete "${stage_dir}/" "${DEPLOY_DIR}/"
else
    warn "rsync not found; using cp fallback for install"
    rm -rf "${DEPLOY_DIR}"
    mkdir -p "${DEPLOY_DIR}"
    cp -a "${stage_dir}/." "${DEPLOY_DIR}/"
fi

apply_deployment_metadata "${stage_dir}" "${ARTIFACT_SOURCE}"
chmod +x "${DEPLOY_DIR}/refresh-token.sh"
if [[ -d "${DEPLOY_DIR}/deploy" ]]; then
    find "${DEPLOY_DIR}/deploy" -maxdepth 1 -type f -name '*.sh' -exec chmod +x {} +
fi
chmod 644 "${DEPLOY_DIR}/deployment.json"

ok "Dashboard artifact installed to ${DEPLOY_DIR}"
