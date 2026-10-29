---
issue: 2049
summary: "Add validated user-scoped artifact deployment with scope-correct rollback and node-c runbook guidance"
dl_state: "in_review"
next_step: "Submit the reviewed branch through normal CI and the merge queue; qualify deployment separately."
title: "User-Scoped Artifact Deployment"
owner: "codex"
branch: "codex/2081-artifact-provenance"
paths: "`deploy/update-deployed.sh`, `deploy/rollback-deployed.sh`, `deploy/lib.sh`, `docs/runbooks/node-c-dashboard-redeploy.md`, `tests/deploy/test_update_deployed_scope.py`"
---

Branch: codex/rd-2049-user-service. The targeted deployment suite passed 160 tests, including 10 focused scope tests. Parent verification with the established dashboard virtual environment resolved the temporary environment's consistency collection errors. Canonical shared pre_pr validation is recorded in the PR; semgrep is unavailable and advisory-skipped, while absent fleet scripts are explicitly skipped. node-c's read-only dry run exercised user-manager routing only; no artifact deployment or credential acceptance was performed. Live recovery remains dependent on an approved artifact, reviewed tooling and a suitable GitHub App credential.

The original #2050 source is consolidated into #2086 with history preserved. Four original CI scope simulations exposed a missing-token lookup abort under pipefail; missing-file and missing-key regression cases now cover the diagnostic correction. Combined validation and deployment acceptance remain separate gates.
