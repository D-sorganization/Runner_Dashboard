---
issue: 1896
summary: "Dedicated per-runner PIP_CACHE_DIR to prevent cache races, with writability health probe"
dl_state: "in_review"
next_step: "Push branch, open PR, and arm auto-merge via automerge_guard.py"
title: "fix(runners): dedicated per-runner PIP_CACHE_DIR to prevent cache races and collisions"
owner: "antigravity"
branch: "fix/1896-runner-pip-cache"
paths: "backend/readiness.py,deploy/configure-runner-pipcache.sh,deploy/runner-hooks/job-started.sh,deploy/runner-cleanup.sh,deploy/install-runner-maintenance.sh,.github/workflows/ci-nightly.yml,tests/api/test_runner_pip_cache_probe.py,tests/deploy/test_runner_pip_cache_lifecycle.py,tests/test_today_deploy_hardening.py"
---
