---
type: feat
---
Implement Private Mode PM-12 separation of duties for declassification and cloud dispatch approvals (`SeparationApproval`, `issue_approval`, `verify_approval`, `declassify_envelope`, `authorize_cloud_dispatch`), enforcing a distinct approver principal, binding to scope/destination/expiry/policy version, and auditing every issuance and verification (#3263).
