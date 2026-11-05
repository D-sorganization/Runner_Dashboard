---
issue: 3892
summary: "Claim guard: the 72h aging-PR rule now blocks a claim only for an aging PR in the claim's own repository, and when the claim names paths (new optional ClaimBody.paths) only one whose changed files overlap them; the 409 message names the PR, repo and overlapping paths. Cross-repo held/draft PRs no longer block unrelated claims."
branch: "claude/3892-claim-guard-scope"
---
