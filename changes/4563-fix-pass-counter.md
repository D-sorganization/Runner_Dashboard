---
issue: 4563
summary: "Escalate merge shepherd after one failed review or CI fix pass"
---
The shepherd counts failed heads in the open cycle. A head fails on a non-approve
review or a CI failure, and the same head counts once. The first failure still
allows one fix. The next failed head skips auto-arm with `fix_pass_cap` and a
Barb escalation notice, including when the current checks are red. A fresh
exact-head PASS still arms. Carry-forward remains advisory and is not an arm path.
