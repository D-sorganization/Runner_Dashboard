---
issue: 3622
summary: "Keep a failing opens_pr resolver from crashing a finished staff run"
paths: "backend/staff/runner_finish.py, tests/api/test_staff_runner.py"
---
`finish_run_after_execution` classified the run with `_opens_pr_or_false`, then
called `opens_pr()` again. The second call re-raised and the worker recorded
`failed`. Classification and the later verify step now share one guarded result.
