---
issue: 3591
summary: "Reject Codex result nudges from a different or missing thread while preserving transcript and usage evidence"
dl_state: "in_review"
next_step: "Review the repair, qualify its release, then verify one bounded real Codex worker task."
branch: "codex/cli-worker-contracts"
---

The result nudge now verifies the observed Codex thread against the requested
original session. Mismatch diagnostics retain both thread identifiers; output
and usage remain available even when the result is rejected.

Validation: new fake-stream and execution-path regressions failed before the
repair; focused continuity, initial-session and extraction tests pass afterward.
Live worker acceptance requires a qualified deployment and remains pending.

Also fixes #3592, the required Windows push-hook portability prerequisite:
unavailable Unix sockets raise OSError at the existing fail-closed WhoIs
boundary; no alternate transport or trust policy is introduced.
