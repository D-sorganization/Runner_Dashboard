---
issue: 1895
summary: "Recreate TMPDIR before each job and exclude _tmp and _pip-cache from workdir cleanup"
dl_state: "in_review"
next_step: "Push branch, open PR, and arm auto-merge via automerge_guard.py"
title: "fix(runners): ensure TMPDIR exists before jobs run and exclude from workdir cleanup"
owner: "antigravity"
branch: "fix/1895-runner-tmpdir"
paths: "deploy/runner-hooks/job-started.sh,deploy/runner-cleanup.sh,tests/deploy/test_runner_tmpdir_lifecycle.py,tests/test_today_deploy_hardening.py"
---
