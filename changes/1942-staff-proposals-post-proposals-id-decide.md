---
issue: 1942
summary: "Staff proposals: POST /proposals/{id}/decide now evaluates the caller's approval authority with the same evaluator as the GET preview (staff.proposal_preview.evaluate_caller_authority) and refuses with 403 + coded error (owner_required, scope_missing, action_scope_missing, role_not_permitted; expired -> 400) before any state change or hash check; the evaluated verdict is stored in the proposal_approve/proposal_deny audit detail and refused attempts are audited as proposal_decide_refused. OpenAPI snapshot regenerated (decide description only)."
dl_state: "in_review"
next_step: "Review and merge the PR."
branch: "fix/1942-decide-authority"
---
