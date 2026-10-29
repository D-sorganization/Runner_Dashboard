---
issue: 2089
summary: "Restore Explicit Loopback Fleet Reads Without Credential Fallback"
dl_state: "in_review"
next_step: "Qualify the combined source, merge through the queue, then verify actual local fleet reads on node-c."
title: "Explicit Loopback Fleet Reads"
owner: "codex"
branch: "codex/2081-artifact-provenance"
---

Use the existing opted-in loopback principal for unauthenticated GET/HEAD fleet reads. Present credentials prevent this fallback; real transport addresses and existing scope checks remain authoritative. HTTP regression cases cover core fleet views, invalid credentials, remote forwarding, flag-disabled requests, scoped principals and denied mutation. Source tests do not establish deployed acceptance.
