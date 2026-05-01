#!/usr/bin/env bash
# check_ts_nocheck.sh — CI ratchet that prevents @ts-nocheck from spreading.
#
# Counts files under frontend/src/ that contain @ts-nocheck and compares
# against the committed baseline in scripts/.ts_nocheck_count.
# Fails if the count has grown (new suppressions added).
# Succeeds (and updates the baseline) if the count has shrunk or stayed the same.
#
# Usage in CI:
#   bash scripts/check_ts_nocheck.sh
#
# Usage locally to accept a lower count after extraction:
#   bash scripts/check_ts_nocheck.sh --update
set -euo pipefail

BASELINE_FILE="$(dirname "$0")/.ts_nocheck_count"
UPDATE_MODE=0

for arg in "$@"; do
  if [ "$arg" = "--update" ]; then
    UPDATE_MODE=1
  fi
done

# Count files (not lines) that contain @ts-nocheck anywhere.
NEW_COUNT=$(grep -rl "@ts-nocheck" frontend/src/ 2>/dev/null | wc -l | tr -d ' ')

if [ ! -f "$BASELINE_FILE" ]; then
  echo "No baseline found at $BASELINE_FILE; writing initial baseline of $NEW_COUNT"
  echo "$NEW_COUNT" > "$BASELINE_FILE"
  exit 0
fi

PREV_COUNT=$(cat "$BASELINE_FILE" | tr -d '[:space:]')

echo "@ts-nocheck file count: baseline=$PREV_COUNT  current=$NEW_COUNT"

if [ "$NEW_COUNT" -gt "$PREV_COUNT" ]; then
  echo "::error::@ts-nocheck count GREW: $PREV_COUNT -> $NEW_COUNT"
  echo "Do not add new @ts-nocheck suppressions. Extract the code to a typed file instead."
  grep -rl "@ts-nocheck" frontend/src/ || true
  exit 1
fi

if [ "$NEW_COUNT" -lt "$PREV_COUNT" ] || [ "$UPDATE_MODE" -eq 1 ]; then
  echo "Count decreased or --update passed. Updating baseline: $PREV_COUNT -> $NEW_COUNT"
  echo "$NEW_COUNT" > "$BASELINE_FILE"
fi

echo "Ratchet check passed."
