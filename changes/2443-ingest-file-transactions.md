---
issue: 2443
summary: "Keep PDF extraction outside SQLite writes and commit atomic files independently during bounded ingest."
---

Visible run bookkeeping and interrupted-run cleanup; completed file progress survives later failures.
