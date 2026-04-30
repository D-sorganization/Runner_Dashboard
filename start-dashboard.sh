#!/usr/bin/env bash
# ==============================================================================
# start-dashboard.sh — Launch the Runner Dashboard
# ==============================================================================
# Usage:
#   ./start-dashboard.sh               # Start on default port 8321
#   ./start-dashboard.sh --port 9000
#   ./start-dashboard.sh --bg          # Run in background
#   ./start-dashboard.sh --reload      # Hot-reload (uvicorn --reload)
#   ./start-dashboard.sh --mock        # Point GH calls at local fixtures (TODO)
#
# Dependencies are installed into a project-local virtualenv at .venv/.
# This script never touches the system Python site-packages.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="${SCRIPT_DIR}/backend"
VENV_DIR="${SCRIPT_DIR}/.venv"
REQUIREMENTS_FILE="${BACKEND_DIR}/requirements.txt"
INSTALL_STAMP="${VENV_DIR}/.installed-stamp"
PORT="${DASHBOARD_PORT:-8321}"
HOST="${DASHBOARD_HOST:-127.0.0.1}"
BACKGROUND=false
RELOAD=false
MOCK=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --port) PORT="$2"; shift 2 ;;
        --host) HOST="$2"; shift 2 ;;
        --bg|--background) BACKGROUND=true; shift ;;
        --reload) RELOAD=true; shift ;;
        --mock) MOCK=true; shift ;;
        --help|-h)
            echo "Usage: $0 [--port N] [--host H] [--bg] [--reload] [--mock]"
            echo "  --port N   Port to serve on (default: 8321)"
            echo "  --host H   Host/interface to bind (default: 127.0.0.1)"
            echo "  --bg       Run in background"
            echo "  --reload   Enable uvicorn hot reload (watches backend/)"
            echo "  --mock     Point GH calls at local fixtures (see TODO below)"
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

export DASHBOARD_PORT="${PORT}"
export DASHBOARD_HOST="${HOST}"
export GITHUB_ORG="${GITHUB_ORG:-D-sorganization}"
export NUM_RUNNERS="${NUM_RUNNERS:-12}"
export MAX_RUNNERS="${MAX_RUNNERS:-16}"

# --mock: TODO — wire DASHBOARD_MOCK_MODE to a fixtures backend. The backend
# currently has no GH-fixture shim; flagging here so the dev surface is ready
# when fixtures land. Tracked alongside #416 for a follow-up.
if [[ "$MOCK" == "true" ]]; then
    export DASHBOARD_MOCK_MODE=1
    echo "[INFO] --mock requested: setting DASHBOARD_MOCK_MODE=1 (backend wiring TODO)"
fi

# ------------------------------------------------------------------
# Ensure project-local virtualenv exists and dependencies are present.
# Never installs into system site-packages.
# ------------------------------------------------------------------
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    echo "[INFO] Creating virtualenv at ${VENV_DIR}"
    python3 -m venv "${VENV_DIR}"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

# Cached install: only re-run pip if requirements.txt is newer than the stamp.
if [[ ! -f "${INSTALL_STAMP}" ]] || [[ "${REQUIREMENTS_FILE}" -nt "${INSTALL_STAMP}" ]]; then
    echo "[INFO] Installing dependencies from ${REQUIREMENTS_FILE}"
    python -m pip install --upgrade pip >/dev/null
    if [[ -f "${REQUIREMENTS_FILE}" ]]; then
        python -m pip install -r "${REQUIREMENTS_FILE}"
    else
        # Fallback for minimal environments without a requirements.txt yet.
        python -m pip install fastapi 'uvicorn[standard]'
    fi
    touch "${INSTALL_STAMP}"
fi

echo ""
echo "  ╔════════════════════════════════════════════╗"
echo "  ║   D-sorganization Runner Dashboard         ║"
echo "  ║   http://localhost:${PORT}                    ║"
echo "  ║   API docs: http://localhost:${PORT}/docs     ║"
echo "  ╚════════════════════════════════════════════╝"
echo ""

run_server() {
    if [[ "$RELOAD" == "true" ]]; then
        # Hot-reload via uvicorn watching backend/. Assumes backend.server:app.
        exec python -m uvicorn server:app \
            --host "${HOST}" \
            --port "${PORT}" \
            --reload \
            --reload-dir "${BACKEND_DIR}" \
            --app-dir "${BACKEND_DIR}"
    else
        exec python "${BACKEND_DIR}/server.py"
    fi
}

if [[ "$BACKGROUND" == "true" ]]; then
    nohup bash -c "$(declare -f run_server); run_server" \
        > /tmp/runner-dashboard.log 2>&1 &
    echo "Dashboard started in background (PID: $!)"
    echo "Logs: /tmp/runner-dashboard.log"
    echo "Stop with: kill $!"
else
    run_server
fi
