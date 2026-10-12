---
issue: 1800
summary: "BR-06 PR 2: relay wiring - a replica stages run-card updates in the durable outbox with a per-run version and a drain loop (no local conversation write); the authority's relay-card ingest dedupes on (run_id, version) and fences stale epochs (409 stale_version / stale_authority_epoch, replica 503 not_authority); GET /api/v1/staff/barb/status. Refs 1800. Replica write proxy and degraded UI deferred."
dl_state: "in_review"
next_step: "PR 2b: replica write proxy + degraded banner; PR 3: failover runbook/script and close 1800."
branch: "feat/1800-pr2-authority"
---
