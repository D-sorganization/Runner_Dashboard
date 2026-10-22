---
issue: 2199
summary: "Scope Fleet staff GitHub App tokens to the validated bare repository name and reject unscoped or foreign-owner requests."
dl_state: "in_review"
next_step: "Merge the qualified token-scope fix and retry the Brick repository-scoped dashboard canary."
branch: "fix/brick-scoped-token-2199"
paths: "backend/staff/github_run_env.py,tests/api/test_fleet_staff_run_token.py,tests/unit/test_staff_github_run_env.py"
---
