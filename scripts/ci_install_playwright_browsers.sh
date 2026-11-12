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

launch_chromium() {
  node -e "require('playwright').chromium.launch().then(b=>b.close()).catch(()=>process.exit(1))"
}

if launch_chromium; then
  exit 0
fi

if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
  echo "Playwright Chromium launch failed; attempting passwordless install-deps for ${browser}..."
  if sudo -n npx playwright install-deps "$browser"; then
    launch_chromium && exit 0
  fi
fi

echo "::error::This self-hosted fleet runner is missing Playwright system dependencies for ${browser}. An admin should run: sudo npx playwright install-deps ${browser}"
exit 1
