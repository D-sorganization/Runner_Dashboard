---
issue: 1800
summary: "BR-06 slice 4 durability: synchronous=FULL on RunStore and StaffAuditStore, typed BarbConfigError for malformed BARB_OUTBOX_*, bounded barb_receipts pruning that keeps the newest-version receipt, explicit OutboxConflictError on a changed payload. Refs 1800."
dl_state: "in_review"
next_step: "Persisted epoch fence; failover runbook and script; wire unused card_version; then close 1800."
branch: "feat/1800-pr4-durability"
---
