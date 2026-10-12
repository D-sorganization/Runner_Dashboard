# ADR 0008: One authoritative Barb endpoint (DRAFT, BR-06 / #1800)

Status: draft. Owner ruling 2026-10-11 (Repository_Management#1853): RPO = 0 (no accepted message lost), RTO = 15 min,
read-only/degraded during an outage, no split writes, single durable authority plus a bounded outbox and
`(run_id, version)` dedupe. Order: #1800, then #1809, then #1810.

## Decision

Exactly one node (the authority) commits Barb conversation state. Every other node is a replica: it never
commits a conversation write locally and delivers worker-produced events through a bounded, persistent outbox.
The authority applies each event with an idempotent, version-gated upsert.

## Defaults pending owner confirmation

1. The authority is the machine-registry `hub` (ControlTower), configurable with `BARB_AUTHORITY_NODE`,
   `BARB_AUTHORITY_URL`, `BARB_AUTHORITY_EPOCH` or a `barb_authority` manifest key. Unconfigured means the legacy
   single-node behaviour (this PR changes nothing until an operator opts in).
2. RPO = 0 is defined against the authority's durable commit (`PRAGMA synchronous=FULL` on the conversation
   connection). Surviving loss of the authority's disk needs a synchronous standby copy. That is a recorded
   follow-up and is NOT implemented.
3. No replica read cache in degraded mode beyond what exists today.
4. Failover is manual: the operator bumps the epoch; the epoch fences the old authority and stale senders.

## Invariants (enforced in code and tests)

- I1 an acknowledged write is durably committed at the authority;
- I2 an `(run_id, version)` is applied at most once (replay = duplicate, same receipt);
- I3 the stored version never decreases; a stale version is rejected; a terminal status is sticky;
- I4 only a request whose epoch equals the authority's epoch is applied;
- I5 every outbox entry is delivered, pending or dead-lettered, never silently dropped; the outbox never exceeds
  `BARB_OUTBOX_MAX_ENTRIES` / `BARB_OUTBOX_MAX_BYTES` (full raises `OutboxFullError`, back-pressure to the producer).

## Schema (additive; old code ignores it)

Conversation migrations 4 `barb_outbox`, 5 `run_card_versions`, 6 `barb_receipts`; `runs.card_version`
(storage-only counter, not part of `RunRecord` or the API).

## Consequences and follow-ups

PR 2 wires the relay, the replica write proxy, `/authority` and `/outbox`, and the degraded UI (after the OpenAPI
snapshot lock is free). PR 3 adds the manual-failover runbook and script and closes #1800. #1809 builds on the
outbox and the per-run version (event revisions); #1810 builds on authority-side reservations sent through
`AuthorityClient`.

## Deferred to PR 2 (from the PR #1935 review; recorded, not fixed in PR 1)

- `barb_receipts` has no pruning, so it grows without bound.
- `synchronous=FULL` is set only on the `ConversationStore` connection; `RunStore` and `StaffAuditStore` stay at the default.
- There is no persisted epoch fence: the epoch comes from configuration, so a restarted old authority is not refused by stored state.
- `runs.card_version` and `RunStore.next_card_version` are unused until PR 2 wires the relay.
- `bounds_from_env` raises a bare `ValueError` on a malformed `BARB_OUTBOX_*` value.
- Enqueueing the same key with a different payload silently keeps the old entry id.
- `backup_database` copies the main database file and ignores the WAL.
