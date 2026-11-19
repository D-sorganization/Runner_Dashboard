---
issue: 4574
summary: "Remove BOARD_ISSUE=2539, atomic board rollover, reject Closes #board in PRs, import supervisor and claims override fix."
---

Delete hardcoded BOARD_ISSUE=2539 from board.py and ensure claims.py reads configured active board only; update \_board_claim_override so status-only / blocked heartbeats do not reserve work; implement atomic crash-recoverable board rollover at ~500 comments moving retired boards to predecessors; reject Closes/Fixes board in PR bodies; import d-overnight-supervisor.ps1 with blocker-keyed dormancy and 1-card-per-lane dispatch.
