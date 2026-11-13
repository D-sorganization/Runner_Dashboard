---
issue: 3046
summary: "ci(queue): route required merge_group jobs to a reserved d-sorg-fleet-queue runner label via opt-in vars.CI_QUEUE_RUNNER_LABEL switch on ci-standard.yml, frontend-tests.yml, ci-secrets.yml, anti-phantom-merge.yml, and ci-auto-deploy-regression.yml (no-op until the repo variable is set after merge). Implements #2768."
branch: "claude/3046-merge-group-reserved-runners"
---
