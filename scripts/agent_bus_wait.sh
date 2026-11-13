#!/usr/bin/env bash
# Usage: agent_bus_wait.sh WAKE_FILE SECONDS
# Sleep up to SECONDS, returning early (exit 0) as soon as WAKE_FILE changes; exit 1 on timeout.
# Drop-in replacement for a lane's backoff `sleep` (#3682 S2).
set -u
wake="$1"; secs="${2:-60}"
mtime() { stat -c %y "$wake" 2>/dev/null || stat -f %m "$wake" 2>/dev/null || echo 0; }
start="$(mtime)"
end=$((SECONDS + secs))
while [ "$SECONDS" -lt "$end" ]; do
  sleep 1
  [ "$(mtime)" != "$start" ] && exit 0
done
exit 1
