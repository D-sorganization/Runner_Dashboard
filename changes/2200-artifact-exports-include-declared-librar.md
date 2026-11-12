---
issue: 2200
summary: "Artifact exports include declared library dependencies; staged startup imports fail before live mutation and updates preserve SQLite/audit/telemetry state"
dl_state: "in_review"
next_step: "Pass protected source checks and publish a self-contained release; replace the documented node-a dependency overlay"
branch: "bot/issue-2199-runtime-integration"
---

node-a deployment acceptance is tracked separately in #2199. The stock 91bc519 artifact failed startup without pypdf and was rolled back; exact source-lock-pinned dependencies restored service on retry. Keep private runtime/state backups. This source change preserves legacy artifact compatibility and does not alter provider auth.
