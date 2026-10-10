---
issue: 1989
summary: "Fork PRs never run on the self-hosted fleet: job-level fork guard on every fleet-capable pull_request/workflow_call job, hosted routing for the required quality-gate/tests lane, PR head never checked out on pull_request_target; vendored scripts/fork_pr_runner_guard.py (from Tools #5427) plus tests/test_fork_pr_runner_guard.py enforce it (Repository_Management#1989)."
---
