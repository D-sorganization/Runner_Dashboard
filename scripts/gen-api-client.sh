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
elif [ -f "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_CMD=("$ROOT_DIR/.venv/bin/python")
elif [ -f "$ROOT_DIR/.venv/Scripts/python.exe" ]; then
  PYTHON_CMD=("$ROOT_DIR/.venv/Scripts/python.exe")
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
val_props = schema.get("components", {}).get("schemas", {}).get("ValidationError", {}).get("properties")
if isinstance(val_props, dict):
    if "ctx" not in val_props:
        val_props["ctx"] = {"title": "Context", "type": "object"}
    if "input" not in val_props:
        val_props["input"] = {"title": "Input"}
out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

if [ -f "$ROOT_DIR/node_modules/prettier/bin/prettier.cjs" ]; then
  node "$ROOT_DIR/node_modules/prettier/bin/prettier.cjs" --parser json --write "$TMP_SNAPSHOT"
else
  npx prettier --parser json --write "$TMP_SNAPSHOT"
fi
if [ -f "$ROOT_DIR/node_modules/openapi-typescript/bin/cli.js" ]; then
  node "$ROOT_DIR/node_modules/openapi-typescript/bin/cli.js" "$TMP_SNAPSHOT" --output "$TMP_TYPES"
else
  npx openapi-typescript "$TMP_SNAPSHOT" --output "$TMP_TYPES"
fi

if [ "$CHECK" -eq 1 ]; then
  diff -u "$SNAPSHOT" "$TMP_SNAPSHOT"
  # Maxwell routes stay out of the published OpenAPI snapshot (#2830) but remain in
  # api-types.ts for hand-maintained client calls; only the JSON contract is gated.
else
  mkdir -p "$(dirname "$SNAPSHOT")"
  cp "$TMP_SNAPSHOT" "$SNAPSHOT"
  cp "$TMP_TYPES" "$TYPES"
fi
