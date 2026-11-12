---
issue: 3861
summary: "Staff Console e2e: one CI retry, granted only when the failed attempt recorded a Chromium net::ERR_NETWORK_CHANGED abort (host interface churn on shared fleet runners aborts Vite dev-server module loads); every other failure still fails first time and retries are annotated network-changed-retry."
dl_state: "in_review"
next_step: "Merge via queue; watch main Frontend Tests for network-changed-retry annotations"
branch: "claude/3861-network-changed-retry"
---
