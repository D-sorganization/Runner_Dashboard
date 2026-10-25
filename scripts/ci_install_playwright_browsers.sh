#!/usr/bin/env bash
# Install Playwright browsers for CI without invoking sudo on self-hosted runners.
set -euo pipefail

browser="${1:-chromium}"
runner_env="${RUNNER_ENVIRONMENT:-self-hosted}"

if [[ "$runner_env" == "github-hosted" ]]; then
  npx playwright install "$browser" --with-deps
  exit 0
fi

npx playwright install "$browser"

if ! npx playwright install-deps "$browser" --dry-run; then
  echo "::error::This self-hosted fleet runner is missing Playwright system dependencies for ${browser}. An admin should run: sudo npx playwright install-deps ${browser}"
  exit 1
fi
