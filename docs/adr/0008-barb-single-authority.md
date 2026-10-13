# ADR 0008: One authoritative Barb endpoint (BR-06 / #1800)

Status: accepted (the defaults below are binding per the owner; the UI-gated items are follow-ups). Owner ruling 2026-10-11 (Repository_Management#1853): RPO = 0 (no accepted message lost), RTO = 15 min,
read-only/degraded during an outage, no split writes, single durable authority plus a bounded outbox and
`(run_id, version)` dedupe. Order: #1800, then #1809, then #1810.

## Decision

Exactly one node (the authority) commits Barb conversation state. Every other node is a replica: it never
commits a conversation write locally and delivers worker-produced events through a bounded, persistent outbox.
The authority applies each event with an idempotent, version-gated upsert.

## Defaults (binding)

1. The authority is the machine-registry `hub` (ControlTower), configurable with `BARB_AUTHORITY_NODE`,
   `BARB_AUTHORITY_URL`, `BARB_AUTHORITY_EPOCH` or a `barb_authority` manifest key. Unconfigured means the legacy
   single-node behaviour (this PR changes nothing until an operator opts in).
2. RPO = 0 is defined against the authority's durable commit (`PRAGMA synchronous=FULL` on the conversation
   connection). Surviving loss of the authority's disk needs a synchronous standby copy. That is a recorded
   follow-up and is NOT implemented.
3. No replica read cache in degraded mode beyond what exists today.
4. Failover is manual: the operator bumps the epoch; the persisted epoch fence refuses the old authority, and the
   epoch check refuses stale senders. Procedure: [Barb authority failover runbook](../operations/barb-authority-failover.md)
   and `scripts/barb_failover.py`.
5. Fleet clients read from the authority: with no `FLEET_API_URL`, `FleetClient` uses `BARB_AUTHORITY_URL`.

## Invariants (enforced in code and tests)

- I1 an acknowledged write is durably committed at the authority;
- I2 an `(run_id, version)` is applied at most once (replay = duplicate, same receipt);
- I3 the stored version never decreases; a stale version is rejected; a terminal status is sticky;
- I4 only a request whose epoch equals the authority's epoch is applied, and an authority whose epoch is below
  the persisted fence, or that names a new node at the fence's epoch, applies nothing;
- I5 every outbox entry is delivered, pending or dead-lettered, never silently dropped; the outbox never exceeds
  `BARB_OUTBOX_MAX_ENTRIES` / `BARB_OUTBOX_MAX_BYTES` (full raises `OutboxFullError`, back-pressure to the producer).

## Schema (additive; old code ignores it)

Conversation migrations 4 `barb_outbox`, 5 `run_card_versions`, 6 `barb_receipts`, 7 `barb_epoch_fence`; `runs.card_version`
(storage-only counter, not part of `RunRecord` or the API).

## Consequences and follow-ups

PR 2 wires the relay, the replica write proxy, `/authority` and `/outbox`, and the degraded UI (after the OpenAPI
snapshot lock is free). PR 3 adds the manual-failover runbook and script and closes #1800. #1809 builds on the
outbox and the per-run version (event revisions); #1810 builds on authority-side reservations sent through
`AuthorityClient`.

## Deferred to PR 2 (from the PR #1935 review; recorded, not fixed in PR 1)

- ~~`barb_receipts` has no pruning, so it grows without bound.~~ Fixed: `prune_barb_receipts` runs a bounded batch every 64 applied updates (`BARB_RECEIPT_MAX_ROWS`, `BARB_RECEIPT_MAX_AGE_DAYS`) and never removes the newest-version receipt of a run, so dedupe and stale rejection are unchanged (`tests/unit/test_barb_durability.py`).
- ~~`synchronous=FULL` is set only on the `ConversationStore` connection; `RunStore` and `StaffAuditStore` stay at the default.~~ Fixed: all three stores set `synchronous=FULL`.
- ~~There is no persisted epoch fence: the epoch comes from configuration, so a restarted old authority is not refused by stored state.~~ Fixed: migration 7 stores a monotonic `(epoch, node)` fence, advanced in the same transaction as each epoch-aware write and by `scripts/barb_failover.py advance`; a stale epoch or an un-bumped node change is refused and a fenced authority stages its own card in the outbox (`tests/unit/test_barb_epoch_fence.py`).
- ~~`runs.card_version` and `RunStore.next_card_version` are unused until PR 2 wires the relay.~~ Fixed in PR #1943: the replica producer stamps each outbox entry with `next_card_version`.
- ~~`bounds_from_env` raises a bare `ValueError` on a malformed `BARB_OUTBOX_*` value.~~ Fixed: it raises `BarbConfigError` (a `ValueError` subclass) naming the variable and value.
- ~~Enqueueing the same key with a different payload silently keeps the old entry id.~~ Fixed: an identical replay stays idempotent; a different payload raises `OutboxConflictError` (I2: a `(run_id, version)` is one event).
- ~~`backup_database` copies the main database file and ignores the WAL.~~ Fixed: it now uses the SQLite online
  backup API, so a backup of a live authority keeps WAL-resident IDs and receipts
  (`tests/unit/test_barb_backup_restore.py`).

## Follow-ups after #1800 (not implemented)

- Degraded banner in the Console while the authority is unreachable (needs a UI decision).
- Replica write proxy for user-originated thread writes (needs a UI decision on what a replica shows and
  accepts during an outage). Until then, point clients and browsers at the authority URL.
- Synchronous standby copy, so RPO = 0 also survives loss of the authority's disk.
- Automatic failover. The owner ruling keeps failover manual.
