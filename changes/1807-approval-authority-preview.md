---
issue: 1807
summary: "Staff proposals: GET /api/v1/staff/proposals/{id} previews the caller's effective approval authority (can_approve, denial_reason) and the params_hash of the reviewed action; ActionCard disables Approve and Deny with an accessible reason; approving and executing now REQUIRE the reviewed params_hash (428 params_hash_required, 409 params_changed, both before any state change). One evaluator (staff.approval_authority) serves preview and execution. No mandates (BR-08). The assistant execute-action route now fails closed on a params_hash failure (428/409/422, nothing runs); unhashable params give 422 hash_unavailable instead of 500."
dl_state: "in_review"
next_step: "Review and merge the PR."
branch: "feat/1807-approval-authority-preview"
---
