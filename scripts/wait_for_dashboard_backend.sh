#!/usr/bin/env bash
# Poll dashboard liveness for CI/E2E backend startup (no GitHub I/O on /livez).
set -euo pipefail

port="${1:-${DASHBOARD_PORT:-}}"
max_seconds="${2:-90}"
log_file="${3:-}"

if [[ -z "${port}" ]]; then
  echo "usage: wait_for_dashboard_backend.sh <port> [max_seconds] [log_file]" >&2
  exit 2
fi

url="http://127.0.0.1:${port}/livez"
for i in $(seq 1 "${max_seconds}"); do
  if curl -sf "${url}" > /dev/null 2>&1; then
    echo "Backend ready on /livez after ${i}s"
    exit 0
  fi
  sleep 1
done

echo "Backend did not become ready on ${url} within ${max_seconds}s" >&2
if [[ -n "${log_file}" && -f "${log_file}" ]]; then
  echo "--- backend log tail (${log_file}) ---" >&2
  tail -n 80 "${log_file}" >&2 || true
fi
exit 1
