---
issue: 3183
summary: "Offload staff thread SQLite reads from async route handlers"
dl_state: "in_review"
next_step: "Merge after required CI checks pass."
branch: "codex/3183-offload-async-io"
---

Synthetic local benchmark: 160 requests at concurrency 8, 200 seeded threads,
and a fixed 5 ms storage delay. Thread-list p95 changed from 738.39 ms to
568.60 ms; concurrent `/api/health` p95 changed from 2367.46 ms to 399.65 ms.
This TestClient workload demonstrates event-loop responsiveness under a
simulated storage wait; it is not a production latency estimate.
