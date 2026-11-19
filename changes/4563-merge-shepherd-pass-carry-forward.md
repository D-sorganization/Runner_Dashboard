---
issue: 4563
summary: "Carry exact-head PASS across main-only merges via diff fingerprint; cap fix passes at one before escalation"
paths: "backend/coordination/merge_shepherd_carry_forward.py, tests/unit/test_merge_shepherd_carry_forward_4563.py"
---
Fingerprints now cover `(path, git_mode, blob_oid)` so an executable-bit change never
carries an old PASS, and `shepherd_arm_eligibility` accepts optional
`head_fingerprint`/`pass_fingerprints` to honor carry-forward. The fingerprint producer
and persisted fix-pass counter remain open under #4563.
