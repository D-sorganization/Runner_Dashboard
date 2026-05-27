#!/bin/bash
# Write deployment metadata for dashboard tracking and rollback

set -euo pipefail

DEPLOYED_DIR="${1:-$HOME/actions-runners/dashboard}"
REPO_DIR="${2:-.}"

if [[ -f "$REPO_DIR/VERSION" ]]; then
  SOURCE_DIR="$REPO_DIR"
elif [[ -f "$REPO_DIR/runner-dashboard/VERSION" ]]; then
  SOURCE_DIR="$REPO_DIR/runner-dashboard"
else
  SOURCE_DIR="$REPO_DIR"
fi

# Read the first non-comment version line from the repo VERSION file
VERSION=$(
  python3 - "$SOURCE_DIR/VERSION" <<'PY'
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
git_value() {
  local git_dir=""
  if [[ -f "$SOURCE_DIR/.git" ]]; then
    git_dir=$(sed -n 's/^gitdir: //p' "$SOURCE_DIR/.git" | head -n 1)
    if [[ "$git_dir" =~ ^([A-Za-z]):/(.*)$ ]]; then
      local drive="${BASH_REMATCH[1],,}"
      git_dir="/mnt/${drive}/${BASH_REMATCH[2]}"
    elif [[ -n "$git_dir" && "$git_dir" != /* ]]; then
      git_dir="$SOURCE_DIR/$git_dir"
    fi
  fi

  if [[ -n "$git_dir" ]]; then
    git --git-dir="$git_dir" --work-tree="$SOURCE_DIR" "$@" 2>/dev/null || true
  else
    git -C "$SOURCE_DIR" "$@" 2>/dev/null || true
  fi
}

GIT_SHA=$(git_value rev-parse HEAD)
GIT_BRANCH=$(git_value rev-parse --abbrev-ref HEAD)
GIT_STATUS=$(git_value status --porcelain)
GIT_SHA=${GIT_SHA:-unknown}
GIT_BRANCH=${GIT_BRANCH:-unknown}
if [[ "$GIT_STATUS" == "__git_unavailable__" ]]; then
  GIT_DIRTY=true
else
  GIT_DIRTY=$([ -z "$GIT_STATUS" ] && echo "false" || echo "true")
fi
DEPLOYED_AT=$(date -Iseconds)
HOSTNAME=$(hostname)
ARTIFACT_SCHEMA="${RUNNER_DASHBOARD_ARTIFACT_SCHEMA:-runner-dashboard-artifact-v1}"
PYTHON_REQUIRES="${RUNNER_DASHBOARD_PYTHON_REQUIRES:->=3.11}"
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
  "deployed_from": "$SOURCE_DIR",
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
