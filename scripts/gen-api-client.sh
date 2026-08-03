#!/usr/bin/env bash
set -euo pipefail

CHECK=0
if [ "${1:-}" = "--check" ]; then
  CHECK=1
elif [ "${1:-}" != "" ]; then
  echo "usage: $0 [--check]" >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SNAPSHOT="$ROOT_DIR/frontend/src/lib/openapi.json"
TYPES="$ROOT_DIR/frontend/src/lib/api-types.ts"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
TMP_SNAPSHOT="$TMP_DIR/openapi.json"
TMP_TYPES="$TMP_DIR/api-types.ts"

if [ -n "${PYTHON:-}" ]; then
  PYTHON_CMD=("$PYTHON")
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD=(python3)
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD=(python)
elif command -v py >/dev/null 2>&1; then
  PYTHON_CMD=(py -3)
else
  echo "No Python interpreter found; set PYTHON=/path/to/python." >&2
  exit 127
fi

# When this script is launched from WSL with a Windows Python interpreter,
# translate the two paths passed to Python.  The remaining commands continue
# to use the native shell paths, so hosted Linux behavior is unchanged.
PY_ROOT_DIR="$ROOT_DIR"
PY_TMP_SNAPSHOT="$TMP_SNAPSHOT"
if [[ "${PYTHON_CMD[0]}" == *.exe ]] && command -v wslpath >/dev/null 2>&1; then
  PY_ROOT_DIR="$(wslpath -w "$ROOT_DIR")"
  PY_TMP_SNAPSHOT="$(wslpath -w "$TMP_SNAPSHOT")"
fi

AUTODERIVE_FLEET_NODES=0 \
FLEET_NODES="" \
DASHBOARD_AUTH_REQUIRED=0 \
"${PYTHON_CMD[@]}" - "$PY_ROOT_DIR" "$PY_TMP_SNAPSHOT" <<'PY'
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
out = Path(sys.argv[2])
sys.path.insert(0, str(root / "backend"))

from fastapi.testclient import TestClient  # noqa: E402
from server import app  # noqa: E402

schema = TestClient(app, raise_server_exceptions=False).get("/openapi.json").json()
out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

npx prettier --parser json --write "$TMP_SNAPSHOT"
npx openapi-typescript "$TMP_SNAPSHOT" --output "$TMP_TYPES"

cat >> "$TMP_TYPES" <<'TS'

// ── Client compatibility aliases ─────────────────────────────────────────────
// These aliases keep the hand-written API client on stable names while the
// canonical paths/components/operations surface above is generated from FastAPI.

export interface ApiError {
    detail: string;
}

export interface RunRepository {
    name: string;
    full_name: string;
}

export interface WorkflowRun {
    id: number;
    name: string;
    workflow_name: string;
    status: string;
    conclusion: string | null;
    head_branch: string;
    head_sha: string;
    html_url: string;
    created_at: string;
    updated_at: string;
    run_number: number;
    repository: RunRepository;
    run_attempt?: number;
    actor?: { login: string };
}

export interface RunsResponse {
    runs: WorkflowRun[];
    total_count?: number;
}

export interface QueueItem {
    id: number;
    name: string;
    status: string;
    created_at: string;
    html_url: string;
    repository: string;
    workflow_name?: string;
    head_branch?: string;
}

export interface QueueResponse {
    queue: QueueItem[];
    count: number;
}

export interface CancelRunRequest {
    repo: string;
    run_id: number;
}

export interface CancelWorkflowRequest {
    workflow_name: string;
    repo?: string | null;
}

export interface Runner {
    id: number;
    name: string;
    os: string;
    status: string;
    busy: boolean;
    labels: Array<{ name: string }>;
}

export interface RunnersResponse {
    runners: Runner[];
    total_count: number;
}

export interface FleetStatusResponse {
    nodes: FleetNode[];
    summary: FleetSummary;
}

export interface FleetNode {
    name: string;
    host: string;
    status: string;
    runners: Runner[];
}

export interface FleetSummary {
    total_runners: number;
    online: number;
    offline: number;
    busy: number;
    idle: number;
}

export interface AgentProvider {
    provider_id: string;
    label: string;
    execution_mode: string;
    dispatch_mode: string;
    notes: string;
    experimental: boolean;
    remote: boolean;
    editable: boolean;
}

export interface ProvidersResponse {
    providers: Record<string, AgentProvider>;
}

export interface ProviderAvailability {
    provider_id: string;
    available: boolean;
    status: string;
    detail: string;
}

export interface DispatchRequest {
    provider_id: string;
    run_id: number;
    repo: string;
}

export interface DispatchResponse {
    status: "queued" | "error";
    message: string;
    job_id?: string;
}

export interface QueueDiagnoseResponse {
    stale_count: number;
    details: string;
    recommendations: string[];
}

export interface UserMe {
    login: string;
    name: string | null;
    avatar_url: string;
    html_url: string;
}

export interface StatsResponse {
    total_runs_today: number;
    failed_runs_today: number;
    success_rate: number;
    avg_duration_seconds: number;
}

export interface UsageResponse {
    usage: UsageEntry[];
}

export interface UsageEntry {
    repo: string;
    run_count: number;
    total_minutes: number;
}
TS

if [ "$CHECK" -eq 1 ]; then
  diff -u "$SNAPSHOT" "$TMP_SNAPSHOT"
  diff -u "$TYPES" "$TMP_TYPES"
else
  mkdir -p "$(dirname "$SNAPSHOT")"
  cp "$TMP_SNAPSHOT" "$SNAPSHOT"
  cp "$TMP_TYPES" "$TYPES"
fi
