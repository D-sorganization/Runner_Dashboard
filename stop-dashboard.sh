#!/usr/bin/env bash
# Stop the runner dashboard if running in background
PID=$(pgrep -f "server.py" | head -1)
if [[ -n "$PID" ]]; then
    kill "$PID"
    echo "Dashboard stopped (PID: $PID)"
else
    echo "Dashboard is not running"
fi
