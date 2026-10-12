---
issue: 1800
summary: "BR-06: WAL-safe authority backup - backup_database uses the SQLite online backup API so a backup taken while the authority is live keeps WAL-resident thread IDs, run-card versions and receipts; restore + replay stays deduplicated. Refs 1800."
dl_state: "in_review"
next_step: "Replica write proxy + degraded banner; receipts pruning; persisted epoch fence; failover runbook/script, then close 1800."
branch: "feat/1800-pr3-runcard-outbox"
---
