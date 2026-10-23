---
issue: 2347
summary: "Run startup library ingestion off-loop and enumerate each bounded batch before opening SQLite writes, preserving removal reconciliation only for complete scans."
paths: "backend/library/ingest.py,tests/library/test_library_ingest_responsiveness.py"
---
