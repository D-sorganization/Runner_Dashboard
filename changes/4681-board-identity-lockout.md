---
issue: 4681
summary: "Keep a single-session identity rejection or duplicate app/ comment from marking the whole board incomplete"
paths: "backend/coordination/board.py, backend/coordination/board_pagination.py, tests/api/test_coordination_api.py, tests/unit/test_coordination_board_comment_limit.py"
---

A fully read board stays `complete` when the only defect is one session's
rejected identity change or unregistered sender. Those warnings stay on
`active_conflicts` and `replay_warnings`. Page-limit truncation, pagination
failures, and unclassified read warnings still fail closed. GitHub's `app/`
login prefix is the same author as the unprefixed login, so a duplicate
comment from that display form is not an identity change.
