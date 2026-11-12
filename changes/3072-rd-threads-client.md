---
issue: 3072
summary: "rd-threads: shared message envelope, barb_cli (send/inbox/peers/ask-barb) and a 4-tool stdio MCP server over RD staff threads"
branch: "feat/3072-rd-threads-client"
---

One envelope schema for every rd-barb path; sends are idempotent on a 60 s dedupe key, and inbox/peers fail closed with `missing_scope` when the principal lacks `fleet.read`.
