---
issue: 4269
summary: "Every move into a terminal run status notifies once: transition() calls the run notifier on the winning compare-and-set, so claims, sessions and run cards are cleaned up on all terminal paths (D-06). A blocked run's card reads failed."
paths: "backend/staff/run_status.py, backend/staff/run_terminal.py, backend/staff/run_link.py, backend/staff/runner.py, backend/staff/runner_finish.py, backend/staff/runner_ops.py, backend/staff/lease.py, backend/staff/dispatch_service.py, backend/coordination/cursor_dispatch_lease.py"
---
