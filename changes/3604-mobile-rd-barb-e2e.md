---
issue: 3604
summary: "Align mobile browser coverage with the canonical direct rd-barb role"
dl_state: "in_review"
next_step: "Verify the isolated browser suite and required CI; keep runner bootstrap diagnosis separate."
branch: "codex/mobile-rd-barb-e2e"
---

The mobile chat-home browser test now asserts the exact rd-barb route required
by #3493, verifies the persisted thread is direct and belongs to rd-barb rather
than auto-route barb, and checks the same conversation URL survives reload.

An explicit fake-only rd-barb role makes the case runnable alone without prior
tests creating roster/history state. Product routing and live staff roles are
unchanged. The standalone missing-role and stale-URL failures were reproduced
before the repair. Runner checkout/npm bootstrap failure remains independent.
