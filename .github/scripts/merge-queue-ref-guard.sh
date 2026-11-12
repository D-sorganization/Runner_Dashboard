#!/usr/bin/env bash
# Self-cancel merge_group workflow runs whose gh-readonly-queue ref was deleted.
# Never touches pull_request, push, or runs other than GITHUB_RUN_ID.
set -euo pipefail

if [[ "${GITHUB_EVENT_NAME:-}" != "merge_group" ]]; then
  exit 0
fi

head_ref="${MERGE_GROUP_HEAD_REF:-${GITHUB_HEAD_REF:-}}"
if [[ -z "$head_ref" ]]; then
  head_ref="${GITHUB_REF#refs/heads/}"
fi

if [[ ! "$head_ref" =~ ^gh-readonly-queue/ ]]; then
  exit 0
fi

repo="${GITHUB_REPOSITORY:-}"
run_id="${GITHUB_RUN_ID:-}"
if [[ -z "$repo" || -z "$run_id" ]]; then
  echo "::warning::merge-queue-ref-guard: missing GITHUB_REPOSITORY or GITHUB_RUN_ID; continuing fail-open"
  exit 0
fi

api_ref="heads/${head_ref}"
if command -v python3 >/dev/null 2>&1; then
  encoded=$(MERGE_QUEUE_REF="$api_ref" python3 - <<'PY'
import os
import urllib.parse

print(urllib.parse.quote(os.environ["MERGE_QUEUE_REF"], safe=""))
PY
)
elif command -v jq >/dev/null 2>&1; then
  encoded=$(printf '%s' "$api_ref" | jq -sRr @uri)
else
  echo "::warning::merge-queue-ref-guard: cannot URL-encode ref; continuing fail-open"
  exit 0
fi

set +e
api_output=$(gh api "repos/${repo}/git/ref/${encoded}" 2>&1)
api_status=$?
set -e

if [[ $api_status -eq 0 ]]; then
  echo "merge-queue ref ${head_ref} is still live; continuing."
  exit 0
fi

if echo "$api_output" | grep -qiE '404|not found'; then
  echo "::notice::Queue ref ${head_ref} is gone; cancelling obsolete merge_group run ${run_id}."
  gh run cancel "$run_id" --repo "$repo" >/dev/null 2>&1 || true
  for _ in $(seq 1 30); do
    raw=$(gh run view "$run_id" --repo "$repo" --json status 2>/dev/null || echo "")
    status=$(STATUS_JSON="$raw" python3 - <<'PY' 2>/dev/null || true
import json
import os

try:
    data = json.loads(os.environ.get("STATUS_JSON") or "{}")
except json.JSONDecodeError:
    data = {}
print(data.get("status") or "")
PY
)
    if [[ "$status" == "completed" ]]; then
      break
    fi
    sleep 2
  done
  exit 0
fi

echo "::warning::merge-queue-ref-guard: ref check failed (exit ${api_status}); continuing fail-open"
echo "$api_output"
exit 0
