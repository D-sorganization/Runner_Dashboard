---
issue: 3604
summary: "Align mobile browser coverage with canonical direct rd-barb role"
dl_state: "in_review"
next_step: "Verify Staff Console e2e and merge queue."
branch: "codex/mobile-rd-barb-e2e"
---

The mobile chat-home browser test asserts the exact rd-barb route required by
#3493, verifies the persisted thread is direct and belongs to rd-barb rather
than auto-route barb, and checks the same conversation URL survives reload.

The fake rd-barb role fixture on main keeps the case runnable in isolation.
Product routing is unchanged.

Align docker-build-scan with frontend-required when PR freshness guard skips
heavy Docker scope jobs.
