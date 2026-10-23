---
issue: 2491
summary: "Main-push CI runs are no longer cancelled by concurrency (only superseded PR runs); bot-maintenance checks are non-blocking for auto-deploy"
dl_state: "in_review"
next_step: "Merge the PR, then confirm the next main commit becomes green and released."
branch: "fix/main-push-concurrency"
---

Concurrency of the six push-to-main blocking workflows is now per event: PR runs share a group per ref and cancel when superseded; push and merge_group runs get a group per sha and are never cancelled. `NON_BLOCKING_DEPLOY_CHECK_NAMES` gains the issue/schedule-triggered bot jobs (verify-closure, reap, sweep, ...).
