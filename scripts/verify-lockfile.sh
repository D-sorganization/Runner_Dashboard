#!/usr/bin/env bash
# Verify that package-lock.json is internally consistent and that a clean
# `npm ci` reproduces a working build, with no network fallback surprises.
#
# Background (issue #1085): a 2026-08-04 fleet rollout hit a committed
# lockfile that `npm ci` could install from, but that left an *invalid*
# dependency tree (a nested `vite` copy pulled in by `vitest` required a
# newer `esbuild` peer than the one actually hoisted/installed). npm ci
# exits 0 in that state -- only `npm ls` surfaces the problem -- so a
# lockfile can look fine and still be silently broken. On a slow/offline
# registry, npm's peer-conflict resolution can also flip to a different
# (sometimes unavailable) transitive version between runs, which is what
# briefly pulled in `rolldown@~1.1.5` during that incident.
#
# This script is NOT a GitHub Actions workflow (workflow files are governed
# separately); it's a plain, runnable sanity check for humans and for any
# existing CI step to invoke directly.
#
# Usage:
#   scripts/verify-lockfile.sh           # npm ci + npm ls + npm run build
#   scripts/verify-lockfile.sh --no-build  # skip the build step (faster)
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

RUN_BUILD=1
for arg in "$@"; do
  case "$arg" in
    --no-build) RUN_BUILD=0 ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: $0 [--no-build]" >&2
      exit 2
      ;;
  esac
done

echo "==> Removing node_modules for a clean-state install"
rm -rf node_modules

echo "==> npm ci (must succeed from the committed lockfile alone)"
npm ci

echo "==> npm ls (must report no UNMET/invalid entries)"
if ! npm ls >/tmp/npm-ls-output.$$ 2>&1; then
  echo "FAIL: npm ls reported an inconsistent dependency tree:" >&2
  cat /tmp/npm-ls-output.$$ >&2
  rm -f /tmp/npm-ls-output.$$
  exit 1
fi
rm -f /tmp/npm-ls-output.$$
echo "OK: dependency tree is consistent."

echo "==> Checking package.json/package-lock.json sync"
if ! npm install --package-lock-only --dry-run 2>&1 | grep -qE '^(added 0 packages|up to date)'; then
  # A dry-run install that would still change the lockfile means the
  # committed lockfile has drifted from package.json.
  echo "WARN: 'npm install --package-lock-only --dry-run' suggests the" >&2
  echo "      lockfile may not exactly match package.json. Run 'npm install'" >&2
  echo "      locally and commit the result if this is unexpected." >&2
fi

if [ "$RUN_BUILD" -eq 1 ]; then
  echo "==> npm run build (must succeed with no network access required)"
  npm run build
  echo "OK: build succeeded."
fi

echo "==> Lockfile verification passed."
