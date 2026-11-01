---
issue: 3604
summary: "Align mobile browser coverage with canonical direct rd-barb role"
dl_state: "in_review"
next_step: "Verify isolated browser suite and CI"
branch: "cursor/issue-3604-rd-barb-e2e-5bd8"
---

The mobile chat-home browser test asserts the exact rd-barb route required by
#3493, verifies the persisted thread is direct and belongs to rd-barb rather than
auto-route barb, and checks the same conversation URL survives reload.

An explicit fake-only rd-barb role makes the case runnable alone without prior
tests creating roster or history state. Product routing and live staff roles are
unchanged.
