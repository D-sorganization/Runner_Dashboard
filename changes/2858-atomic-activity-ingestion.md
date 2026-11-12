---
issue: 2858
summary: "Add bounded atomic activity ingestion with principal binding, hub redaction and persistent quotas"
dl_state: "in_review"
next_step: "Merge after CI and the lane arming cap permit"
branch: "codex/2858-activity-ingestion"
---

Test-first coverage verifies durable retries, complete rollback, concurrent
ownership enforcement, persistent quota accounting, redaction and HTTP bounds.
No deployment or release changes.
