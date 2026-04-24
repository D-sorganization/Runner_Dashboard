#!/usr/bin/env bash
# ==============================================================================
# start-dashboard.sh — Launch the Runner Dashboard
# ==============================================================================
# Usage:
#   ./start-dashboard.sh           # Start on default port 8321
#   ./start-dashboard.sh --port 9000
#   ./start-dashboard.sh --bg      # Run in background
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="${SCRIPT_DIR}/backend"
PORT="${DASHBOARD_PORT:-8321}"
BACKGROUND=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --port) PORT="$2"; shift 2 ;;
        --bg|--background) BACKGROUND=true; shift ;;
        --help|-h)
            echo "Usage: $0 [--port N] [--bg]"
            echo "  --port N   Port to serve on (default: 8321)"
            echo "  --bg       Run in background"
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

export DASHBOARD_PORT="${PORT}"
export GITHUB_ORG="${GITHUB_ORG:-D-sorganization}"
export NUM_RUNNERS="${NUM_RUNNERS:-12}"
export MAX_RUNNERS="${MAX_RUNNERS:-16}"

# Check deps
if ! python3 -c "import fastapi" 2>/dev/null; then
    echo "[INFO] Installing FastAPI and uvicorn..."
    pip3 install --break-system-packages fastapi uvicorn[standard]
fi

echo ""
echo "  ╔════════════════════════════════════════════╗"
echo "  ║   D-sorganization Runner Dashboard         ║"
echo "  ║   http://localhost:${PORT}                    ║"
echo "  ║   API docs: http://localhost:${PORT}/docs     ║"
echo "  ╚════════════════════════════════════════════╝"
echo ""

if [[ "$BACKGROUND" == "true" ]]; then
    nohup python3 "${BACKEND_DIR}/server.py" > /tmp/runner-dashboard.log 2>&1 &
    echo "Dashboard started in background (PID: $!)"
    echo "Logs: /tmp/runner-dashboard.log"
    echo "Stop with: kill $!"
else
    python3 "${BACKEND_DIR}/server.py"
fi
