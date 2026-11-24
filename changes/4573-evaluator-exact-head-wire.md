---
issue: 4573
summary: "Wire shepherd_arm_eligibility into evaluate_pr_shepherd_eligibility (#4573 AC4)."
---

Merge-shepherd pass planning now routes exact-head gating through `evaluate_pr_shepherd_eligibility` when `PRCandidate` carries head SHA and review context, instead of calling `shepherd_arm_eligibility` only in `plan_repo_pass`.
