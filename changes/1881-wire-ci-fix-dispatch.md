---
issue: 1881
summary: "RD-1 CI-fix dispatch is wired (#1881, #1879): signed GitHub webhook (workflow_run failures incl. merge-group runs, pull_request dequeued) behind CI_FIX_DISPATCH_ENABLED launches one session per PR on the PR head through the staff dispatch path; locks are shared across workers in SQLite and expire; escalation follows a same-failure streak"
dl_state: "in_review"
next_step: "Merge PR #1887 through the queue, then set GITHUB_WEBHOOK_SECRET and CI_FIX_DISPATCH_ENABLED on one node."
title: "RD-1 CI-fix dispatch wired: webhook trigger, staff launch on the PR head, merge-queue events"
owner: "claude"
branch: "feat/1881-wire-ci-fix-dispatch"
paths: "backend/ci_fix_dispatch.py,backend/ci_fix_events.py,backend/ci_fix_locks.py,backend/ci_fix_service.py,backend/routers/remediation_ci_fix.py,backend/staff/dispatch_service.py,backend/staff/plan.py,backend/staff/runner.py,backend/staff/workspace.py,backend/staff/retry.py,backend/gh_client.py,backend/dispatch_effort.py,backend/middleware.py,tests/api/test_ci_fix_*.py,tests/api/test_staff_pr_head_worktree.py"
---

Worktree /home/user/wt/rd-1881, branch feat/1881-wire-ci-fix-dispatch, PR #1887 (Repository_Management#1889). Design, validation and operator enablement steps are in the PR body. Review follow-up: CI-fix worktrees start from the PR head (RunRequest.head_ref -> add_worktree start_ref) and push to it; locks are an SQLite table shared by all workers (BEGIN IMMEDIATE); escalation counts consecutive dispatches with the same failure signature; a webhook delivery is recorded only after its dispatch is accepted; dequeue reasons are normalised. Open: required vs optional checks are not distinguished; route effort is audited but not passed to the session.
