---
issue: 1800
summary: "BR-06 PR 1: Barb single-authority foundation - migrations 4-6 (barb_outbox, run_card_versions, barb_receipts), runs.card_version, epoch-fenced AuthorityClient, version-gated idempotent run-card upsert replacing the racy post_run_card guard, bounded outbox with backoff/ordering/restart recovery. No public route; inert until BARB_AUTHORITY_* is set. Refs #1800."
dl_state: "in_review"
next_step: "Review PR 1; then PR 2 (wiring, degraded UI, OpenAPI after #1807) and PR 3 (failover runbook, ADR, Closes #1800)."
branch: "feat/1800-barb-authority-foundation"
---
