---
issue: 4563
summary: "Cap merge-shepherd fix passes at one before escalation; carry-forward helpers added as advisory only"
paths: "backend/coordination/merge_shepherd_carry_forward.py, tests/unit/test_merge_shepherd_carry_forward_4563.py"
---

`fix_pass_decision` allows one fix pass, then escalates. `diff_fingerprint` (path, git mode,
blob oid) and `pass_with_carry_forward` are advisory and NOT wired into
`shepherd_arm_eligibility`: ADR 0010 requires fresh acceptance when the integration tree
changes, so arming use needs an ADR amendment. The persisted fix-pass counter remains open
under #4563.
