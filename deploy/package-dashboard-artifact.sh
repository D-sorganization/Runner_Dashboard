#!/usr/bin/env bash
# ==============================================================================
# package-dashboard-artifact.sh — Build a deterministic, versioned deployment artifact.
# ==============================================================================
# This script packages the runner-dashboard into a standalone immutable tarball
# containing frontend static builds, backend application code, deploy scripts,
# and verified deployment metadata for offline installation.
#
# Usage:
#   bash deploy/package-dashboard-artifact.sh [--output-dir PATH] [--version VER] [--sha SHA]
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-${SCRIPT_DIR}/dist}"
VERSION_OVERRIDE=""
SHA_OVERRIDE=""
SKIP_BUILD=false

usage() {
    cat <<'EOF'
Usage:
  package-dashboard-artifact.sh [OPTIONS]

Options:
  --output-dir PATH   Output directory for tarball and checksum (default: ./dist)
  --version VERSION   Override version (default: read from VERSION file)
  --sha SHA           Override git commit SHA (default: git rev-parse HEAD)
  --skip-build        Skip npm build if frontend bundle is already present
  -h, --help          Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --version) VERSION_OVERRIDE="$2"; shift 2 ;;
        --sha) SHA_OVERRIDE="$2"; shift 2 ;;
        --skip-build) SKIP_BUILD=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# Resolve version
if [[ -n "${VERSION_OVERRIDE}" ]]; then
    VERSION="${VERSION_OVERRIDE}"
else
    [[ -f "${SCRIPT_DIR}/VERSION" ]] || { echo "VERSION file not found at ${SCRIPT_DIR}/VERSION" >&2; exit 1; }
    VERSION=$(grep -vE '^\s*(#|$)' "${SCRIPT_DIR}/VERSION" | head -n1 | tr -d '[:space:]')
fi

# Resolve commit SHA
if [[ -n "${SHA_OVERRIDE}" ]]; then
    GIT_SHA="${SHA_OVERRIDE}"
elif [[ -n "${GITHUB_SHA:-}" ]]; then
    GIT_SHA="${GITHUB_SHA}"
elif command -v git >/dev/null 2>&1 && git -C "${SCRIPT_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    GIT_SHA=$(git -C "${SCRIPT_DIR}" rev-parse HEAD 2>/dev/null || echo "unknown")
else
    GIT_SHA="unknown"
fi

GIT_BRANCH="main"
if command -v git >/dev/null 2>&1 && git -C "${SCRIPT_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    GIT_BRANCH=$(git -C "${SCRIPT_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
fi

echo "==> Packaging Runner Dashboard ${VERSION} (${GIT_SHA})"

# Ensure frontend build is up to date
if [[ "${SKIP_BUILD}" != "true" ]]; then
    if [[ ! -d "${SCRIPT_DIR}/node_modules" ]]; then
        echo "==> Installing Node dependencies (npm ci)..."
        (cd "${SCRIPT_DIR}" && npm ci)
    fi
    echo "==> Building production frontend bundle..."
    (cd "${SCRIPT_DIR}" && npm run build)
fi

mkdir -p "${OUTPUT_DIR}"
TMP_DIR="$(mktemp -d)"
cleanup() {
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

STAGE_DIR="${TMP_DIR}/stage"
mkdir -p "${STAGE_DIR}"

echo "==> Staging artifact layout..."
# 1. Base files
cp "${SCRIPT_DIR}/VERSION" "${STAGE_DIR}/VERSION"
[[ -f "${SCRIPT_DIR}/README.md" ]] && cp "${SCRIPT_DIR}/README.md" "${STAGE_DIR}/README.md"
cp "${SCRIPT_DIR}/local_apps.json" "${STAGE_DIR}/local_apps.json"
cp "${SCRIPT_DIR}/deploy/refresh-token.sh" "${STAGE_DIR}/refresh-token.sh"
chmod +x "${STAGE_DIR}/refresh-token.sh"

# 2. Backend source
mkdir -p "${STAGE_DIR}/backend"
if command -v rsync >/dev/null 2>&1; then
    rsync -a --exclude='__pycache__' --exclude='*.pyc' --exclude='.pytest_cache' --exclude='.venv' --exclude='venv' \
        "${SCRIPT_DIR}/backend/" "${STAGE_DIR}/backend/"
else
    cp -r "${SCRIPT_DIR}/backend/." "${STAGE_DIR}/backend/"
    find "${STAGE_DIR}/backend" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
    find "${STAGE_DIR}/backend" -type f -name '*.pyc' -delete 2>/dev/null || true
fi

# 3. Frontend static distribution
mkdir -p "${STAGE_DIR}/frontend"
if [[ -d "${SCRIPT_DIR}/dist" ]]; then
    cp -r "${SCRIPT_DIR}/dist" "${STAGE_DIR}/dist"
    mkdir -p "${STAGE_DIR}/frontend/dist"
    cp -r "${SCRIPT_DIR}/dist/." "${STAGE_DIR}/frontend/dist/"
fi
if [[ -d "${SCRIPT_DIR}/frontend/public" ]]; then
    mkdir -p "${STAGE_DIR}/frontend/public"
    cp -r "${SCRIPT_DIR}/frontend/public/." "${STAGE_DIR}/frontend/public/"
fi
if [[ -f "${SCRIPT_DIR}/frontend/index.html" ]]; then
    cp "${SCRIPT_DIR}/frontend/index.html" "${STAGE_DIR}/frontend/index.html"
fi
if [[ -f "${SCRIPT_DIR}/frontend/icon.svg" ]]; then
    cp "${SCRIPT_DIR}/frontend/icon.svg" "${STAGE_DIR}/frontend/icon.svg"
fi

# 4. Deploy scripts and helpers
mkdir -p "${STAGE_DIR}/deploy"
if command -v rsync >/dev/null 2>&1; then
    rsync -a --exclude='.gitkeep' --exclude='*.pyc' "${SCRIPT_DIR}/deploy/" "${STAGE_DIR}/deploy/"
else
    cp -r "${SCRIPT_DIR}/deploy/." "${STAGE_DIR}/deploy/"
fi

# 5. Config (if present)
if [[ -d "${SCRIPT_DIR}/config" ]]; then
    mkdir -p "${STAGE_DIR}/config"
    cp -r "${SCRIPT_DIR}/config/." "${STAGE_DIR}/config/"
fi

# 6. Generate deployment.json
BUILD_TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date +"%Y-%m-%dT%H:%M:%SZ")
python3 - "${STAGE_DIR}/deployment.json" "${VERSION}" "${GIT_SHA}" "${GIT_BRANCH}" "${BUILD_TIMESTAMP}" <<'PY'
import json
import sys
from pathlib import Path

out_path = Path(sys.argv[1])
version = sys.argv[2]
git_sha = sys.argv[3]
git_branch = sys.argv[4]
build_timestamp = sys.argv[5]

metadata = {
    "app": "runner-dashboard",
    "version": version,
    "git_sha": git_sha,
    "git_branch": git_branch,
    "git_dirty": False,
    "build_timestamp": build_timestamp,
    "source": "github-actions-artifact-build",
    "compatibility": {
        "artifact_schema": "runner-dashboard-artifact-v1",
        "python_requires": ">=3.11",
        "service_name": "runner-dashboard.service"
    }
}
out_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
chmod 644 "${STAGE_DIR}/deployment.json"

# 7. Generate FILES.txt inventory
echo "==> Generating deterministic FILES.txt inventory..."
(cd "${STAGE_DIR}" && find . -type f | sed 's|^\./||' | sort > "${STAGE_DIR}/FILES.txt")

# 8. Create compressed tarball
ARTIFACT_NAME="dashboard-${VERSION}.tar.gz"
ARTIFACT_PATH="${OUTPUT_DIR}/${ARTIFACT_NAME}"
CHECKSUM_PATH="${ARTIFACT_PATH}.sha256"

echo "==> Building tarball ${ARTIFACT_PATH}..."
(cd "${STAGE_DIR}" && tar -czf "${ARTIFACT_PATH}" .)

# 9. Compute and write sha256 checksum
(cd "${OUTPUT_DIR}" && sha256sum "${ARTIFACT_NAME}" > "${CHECKSUM_PATH}")

echo "==> Verifying generated artifact against installer layout validator..."
bash "${SCRIPT_DIR}/deploy/install-dashboard-artifact.sh" --artifact "${ARTIFACT_PATH}" --deploy-dir "${TMP_DIR}/test-install" >/dev/null

echo "✓ Successfully built immutable artifact: ${ARTIFACT_PATH}"
echo "✓ SHA-256: $(cat "${CHECKSUM_PATH}")"
