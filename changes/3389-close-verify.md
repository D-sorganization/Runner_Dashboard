---
issue: 3389
summary: "Both root causes of #3389 are already fixed and merged: #3391 (merge commit 53e6b3b8, sparse-checkout fix for the detect-secrets baseline-diff job) and #3390 (merge commit 0c90f292, Playwright help-dialog focus-race fix). Verified: both merge commits are ancestors of current main (0f7e3fd6); the last 3 merge_group runs of CI Secrets at main tip all report conclusion=success; the last completed push run of Frontend Tests at main tip (97cdfa7b) also reports success. No production code change in this PR; it only closes the issue with its evidence trail, per AGENTS.md's closing-issues rule (merged PR with Closes #N required)."
dl_state: "shipped"
next_step: "none -- issue closes with this PR"
title: "Verify and close: detect-secrets + a11y help-dialog main-red (#3389)"
owner: "claude-k"
branch: "claude/3389-close-verify"
paths: "changes/"
---
