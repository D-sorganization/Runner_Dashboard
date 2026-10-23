---
issue: 2415
summary: "Keep indexed codebase search and metadata reads free of checkout tree scans."
---

## Changed

- Cartographer API freshness compares indexed commits with current Git HEAD without enumerating or hashing checkout files. Missing repositories remain stale; source content scanning stays in pack refresh.
- Search still proves each emitted citation against its committed blob. Dirty working files cannot replace indexed evidence, and dirty text indexed without a matching committed blob remains excluded.

## Validation

- Real Git/SQLite API regressions prohibit file enumeration and pack rebuilding during search and metadata reads; both tests failed before the fix.
