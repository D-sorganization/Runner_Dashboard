---
issue: 4574
summary: "Remove BOARD_ISSUE=2539, atomic board rollover, reject Closes #board in PRs, import supervisor and claims override fix."
---

Delete hardcoded BOARD_ISSUE=2539 from board.py and ensure claims.py reads configured active board only; update \_board_claim_override so status-only / blocked heartbeats do not reserve work; implement atomic crash-recoverable board rollover at ~500 comments moving retired boards to predecessors; reject Closes/Fixes board in PR bodies; import d-overnight-supervisor.ps1 with blocker-keyed dormancy and 1-card-per-lane dispatch.

Recovery follow-up: persist the original board/predecessor snapshot and unique creation marker before the remote create; reconcile uncertain creates by marker without replaying writes, reject invalid/conflicting journals, and recover both sides of the config-write boundary. Ambiguous creation remains journaled for operator reconciliation. Regression tests inject both crash windows, including legacy journal recovery.
