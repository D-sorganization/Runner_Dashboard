---
issue: 2199
summary: "Add an allowlisted Codex Stop hook for bounded shared Barb summaries"
---

Adds a stdlib Codex Stop hook for explicitly allowlisted sessions. The hook requests one bounded, digest-only write through `barb_shared_context.fleet_memory_append_session_digest`; it never reads transcript content or falls back to local storage. Per-session opt-in and manual review of trusted hook configuration remain required.
