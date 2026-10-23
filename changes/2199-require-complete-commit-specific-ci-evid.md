---
issue: 2199
summary: "Require complete commit-specific CI evidence before automatic deployment"
dl_state: "in_review"
next_step: "Merge and qualify the bounded paginated CI inventory before deployment"
branch: "feat/ci-evidence-pagination-2199"
---

Fetch all bounded CI check pages and reject missing, duplicate or wrong-commit evidence. Keep run completion outside role-resolver failures, and compare independent schema dataclasses by their values in the drift regression.
