---
issue: 2049
summary: "Add validated user-scoped artifact deployment with scope-correct rollback and Brick runbook guidance"
dl_state: "in_review"
next_step: "Submit the reviewed branch through normal CI and the merge queue; qualify deployment separately."
title: "User-Scoped Artifact Deployment"
owner: "codex"
branch: "codex/rd-2049-user-service"
paths: "`deploy/update-deployed.sh`, `deploy/rollback-deployed.sh`, `deploy/lib.sh`, `docs/runbooks/brick-dashboard-redeploy.md`, `tests/deploy/test_update_deployed_scope.py`"
---

Branch: codex/rd-2049-user-service. The targeted deployment suite passed 160 tests, including 10 focused scope tests. Parent verification with the established dashboard virtual environment resolved the temporary environment's consistency collection errors. Canonical shared pre_pr validation is recorded in the PR; semgrep is unavailable and advisory-skipped, while absent fleet scripts are explicitly skipped. Brick's read-only dry run exercised user-manager routing only; no artifact deployment or credential acceptance was performed. Live recovery remains dependent on an approved artifact, reviewed tooling and a suitable GitHub App credential.
