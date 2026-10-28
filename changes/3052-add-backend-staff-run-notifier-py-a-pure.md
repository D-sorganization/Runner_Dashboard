---
issue: 3052
summary: "Add backend/staff/run_notifier.py: a pure planner for terminal-run-card notifications (dedupe, 10-min digest, quiet hours, 24h backlog digest, private-repo sink routing) plus an OutboxSink reusing the existing #1800 outbox for idempotent delivery."
branch: "claude/3052-run-card-notifier"
paths: "backend/staff/run_notifier.py,tests/unit/test_staff_run_notifier.py"
---
