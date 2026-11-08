---
issue: 4176
summary: "Fix credential-mirror audit false-positive: byte-identical DrvFs copies with sub-second-truncated mtimes are no longer flagged stale"
branch: "claude/4176-credential-mirror-subsecond-mtime"
paths: "deploy/sync_provider_credentials.py,tests/deploy/test_sync_provider_credentials.py"
---
