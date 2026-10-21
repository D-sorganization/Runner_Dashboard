---
issue: 2049
summary: "Add validated user-scoped artifact deployment with scope-correct rollback and Brick runbook guidance"
dl_state: "in_review"
next_step: "Parent review the local commit and diff before any push."
title: "User-Scoped Artifact Deployment"
owner: "codex"
branch: "codex/rd-2049-user-service"
paths: "`deploy/update-deployed.sh`, `deploy/rollback-deployed.sh`, `deploy/lib.sh`, `docs/runbooks/brick-dashboard-redeploy.md`, `tests/deploy/test_update_deployed_scope.py`"
---

Branch: codex/rd-2049-user-service. Parent review only; no push or deployment. The focused scope suite passes 10/10; canonical `pre_pr` passes lint/format, diff mypy, affected tests, semgrep/import policy (semgrep unavailable, advisory skip), policy/fragment, fleet policy, and workflow gates. Its consistency collection fails with 14 import errors in existing staff tests because `tests.staff_proposal_hash` and `tests.api` do not resolve in this environment. Brick's read-only dry run exercised user-manager routing only; no artifact deployment or credential acceptance was performed.
