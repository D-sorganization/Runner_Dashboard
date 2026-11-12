---
issue: 2199
summary: "Scope Fleet staff GitHub App tokens to the validated bare repository name, reject empty Fleet scopes, and reject foreign-owner requests."
dl_state: "in_review"
next_step: "Merge the qualified token-scope fix and retry the node-c repository-scoped dashboard canary; separately remove installation-wide token minting from local repo-less runs."
branch: "fix/node-c-scoped-token-2199"
paths: "backend/staff/github_run_env.py,tests/api/test_fleet_staff_run_token.py,tests/unit/test_staff_github_run_env.py"
---
