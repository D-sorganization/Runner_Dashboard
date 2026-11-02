#!/usr/bin/env bash
# Apply taxonomy labels to open RD issues listed in rd_unlabeled_issue_labels.json.
# Requires gh authenticated with issue write access (fleet App bot on hub).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MAP="$ROOT/scripts/rd_unlabeled_issue_labels.json"
REPO="${RD_LABEL_REPO:-D-sorganization/Runner_Dashboard}"
while read -r line; do
  num="${line%%:*}"
  num="${num//\"/}"
  labels="$(python3 -c "import json; m=json.load(open('$MAP')); print(','.join(m['$num']))")"
  echo "Issue #$num <- $labels"
  gh issue edit "$num" --repo "$REPO" --add-label "$labels"
done < <(python3 -c "import json; print('\n'.join(json.load(open('$MAP')).keys()))")
