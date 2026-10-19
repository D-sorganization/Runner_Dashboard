---
issue: 1979
summary: "job-started.sh keeps the previous HEAD as refs/runner-cache/last-checkout so self-hosted fetches negotiate a delta instead of the full snapshot (measured 4.56 MiB -> 223 KiB), and removes orphaned fetch temp packs."
---
