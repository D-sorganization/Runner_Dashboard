---
issue: 4563
summary: "Escalate merge shepherd after one failed fix pass in the open review cycle"
---
The shepherd counts non-approve heads in the current review cycle. The first rejection
still allows one fix. A second rejection skips auto-arm with `fix_pass_cap` until a
fresh exact-head PASS. Carry-forward remains advisory and is not an arm path.
