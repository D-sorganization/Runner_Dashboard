---
issue: 3064
summary: "RD role loader reads retry.failover_chain so rd-barb keeps her claude-first chain"
---

`RoleSpec.failover_chain` is parsed from `retry.failover_chain`, validated (shape in the validator, provider membership in parse_role) and serialised. The local schema mirrors Repository_Management's block. Before this, RD rejected the key as unknown and fell back to the codex-first default.
