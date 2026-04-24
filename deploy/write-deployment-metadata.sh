#!/bin/bash
# Write deployment metadata for dashboard tracking and rollback

set -euo pipefail

DEPLOYED_DIR="${1:-$HOME/actions-runners/dashboard}"
REPO_DIR="${2:-.}"

# Read the first non-comment version line from the repo VERSION file
VERSION=$(
  python3 - "$REPO_DIR/runner-dashboard/VERSION" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
try:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            print(stripped)
            break
    else:
        print("unknown")
except OSError:
    print("unknown")
PY
)
GIT_SHA=$(git -C "$REPO_DIR" rev-parse HEAD 2>/dev/null || echo "unknown")
GIT_BRANCH=$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
GIT_DIRTY=$([ -z "$(git -C "$REPO_DIR" status --porcelain)" ] && echo "false" || echo "true")
DEPLOYED_AT=$(date -Iseconds)
HOSTNAME=$(hostname)
ARTIFACT_SCHEMA="${RUNNER_DASHBOARD_ARTIFACT_SCHEMA:-runner-dashboard-artifact-v1}"
PYTHON_REQUIRES="${RUNNER_DASHBOARD_PYTHON_REQUIRES:->=3.10}"
SERVICE_NAME="${RUNNER_DASHBOARD_SERVICE_NAME:-runner-dashboard.service}"

# Create metadata file
mkdir -p "$DEPLOYED_DIR"
cat > "$DEPLOYED_DIR/deployment.json" <<EOF
{
  "version": "$VERSION",
  "git_sha": "$GIT_SHA",
  "git_branch": "$GIT_BRANCH",
  "git_dirty": $GIT_DIRTY,
  "deployed_at": "$DEPLOYED_AT",
  "deployed_from": "$REPO_DIR",
  "hostname": "$HOSTNAME",
  "app": "runner-dashboard",
  "compatibility": {
    "artifact_schema": "$ARTIFACT_SCHEMA",
    "python_requires": "$PYTHON_REQUIRES",
    "service_name": "$SERVICE_NAME"
  },
  "source": "deployment-metadata"
}
EOF

chmod 644 "$DEPLOYED_DIR/deployment.json"
echo "✓ Deployment metadata written to $DEPLOYED_DIR/deployment.json"
