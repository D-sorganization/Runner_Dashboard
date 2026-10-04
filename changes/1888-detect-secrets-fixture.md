---
issue: 1888
summary: "Mark the fixture SHA in both changes_fragment test files with the allowlist pragma and refresh the stale baseline line number so detect-secrets is green again"
dl_state: "shipped"
next_step: "None; the fix ships with this PR"
title: "Allowlist fixture SHA for detect-secrets"
owner: "claude"
branch: "fix/detect-secrets-fragment-test"
paths: "tests/test_changes_fragment_rollout.py"
---

One-line test-fixture pragma. Verified with detect-secrets scan: finding before, none after.
