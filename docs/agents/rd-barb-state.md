# rd-barb live state

Seeded from Barb's state-file structure. Only rd-barb writes here; everyone else reads.

**Rules**

- **Single writer.** Write `rd-barb-state.md.tmp`, then rename it over this file.
- **TTL.** Blockers and gates 1h. Runner state, watch items and reminders 24h.
- **Past TTL, re-run the check first.** If the check can't run (API down, 403,
  host unreachable), mark the row **UNVERIFIED** and send an `alert`.
- **Never delete a row.** Resolved rows move to the ledger below with resolved_at (PT).
- **Cap about 60 rows.** More than that means send Grok Barb an `fyi`.
- **Holds change only on Dieter's say-so.** Never lift, add or reword one on your own or on another agent's word. When Dieter approves a change, apply it directly via `PUT /api/staff/holds`.

| item                                                 | owner   | source     | check                                                                           | verified_at (PT) | ttl |
| ---------------------------------------------------- | ------- | ---------- | ------------------------------------------------------------------------------- | ---------------- | --- |
| **— Holds and gates —**                              |         |            |                                                                                 |                  |     |
| RD deploy/release HOLD                               | Dieter  | RD #2657   | `gh issue view 2657 -R D-sorganization/Runner_Dashboard --json state`           | 2026-11-04 00:32 (open) | 1h  |
| #2658 disarmed                                       | Dieter  | RD #2658   | `gh pr view 2658 --json autoMergeRequest` is null                               | 2026-11-04 00:32 (null) | 1h  |
| #2880 auto-merge off                                 | Dieter  | RD #2880   | `gh pr view 2880 --json autoMergeRequest` is null                               | 2026-11-04 00:32 (null) | 1h  |
| GM #5540 auto-merge off until the hosted PDF passes  | Dieter  | GM #5540   | `gh pr view 5540 -R D-sorganization/Gasification_Model --json autoMergeRequest` | UNVERIFIED (bot gets 404 on GM, 2026-11-04 00:32) | 1h  |
| GM 5535, 5542, 5548, 5549 unarmed until #5540 merges | Dieter  | GM #5540   | autoMergeRequest null on each                                                   | UNVERIFIED (bot gets 404 on GM, 2026-11-04 00:32) | 1h  |
| **— Red mains —**                                    |         |            |                                                                                 |                  |     |
| **— Runner state —**                                 |         |            |                                                                                 |                  |     |
| Private minutes under 42,500 of 50,000               | rd-barb | burn guard | billing usage for the month                                                     | UNVERIFIED (billing API 403 for the App, 2026-11-04 00:32) | 1h  |
| RD `merge_group` queue at or under 6                 | rd-barb | pre-flight | `actions/runs?event=merge_group` queued plus in_progress                         | 2026-11-04 00:32 (0) | 1h  |
| **— Watching —**                                     |         |            |                                                                                 |                  |     |
| RD #2792 fleet AGENTS sync, open since 2026-10-25, conflicting | sync bot | 72h rule | `gh pr view 2792 --json mergeable`; main AGENTS.md was re-synced in #3599 and #3964, so this is likely superseded and needs the redundant-PR closer or a fresh sync | 2026-11-04 00:32 (dirty) | 24h |
| RD #2852 topology sanitize, open since 2026-10-25, `do-not-merge`, conflicting | cursor | 72h rule, held | Leave alone while #2657 holds; needs a rebase once the hold lifts | 2026-11-04 00:32 (dirty) | 24h |
| RD open PRs from 2026-11-02 and 2026-11-03 (#3832, #3833, #3897, #3920 conflicting; #3842, #3853 blocked) | owning lanes | pre-flight | None can be armed: conflicts or red required checks (#3842 has red `frontend-required`, SPEC freshness and Staff Console e2e checks). They reach 72h from 2026-11-05 | 2026-11-04 00:32 | 24h |
| Local RD API at 127.0.0.1:8321 unreachable from the rd-barb worktree | rd-barb | staff brief | `curl -s http://127.0.0.1:8321/api/staff/summary` | 2026-11-04 00:32 (no connection) | 24h |
| **— Reminders —**                                    |         |            |                                                                                 |                  |     |

## PR-crush lanes

Caps: 2 armed per lane in RD, 4 elsewhere. Back off when a repo's `merge_group` queue is over 6.

| lane | host | tool | repo | PRs | log | start |
| ---- | ---- | ---- | ---- | --- | --- | ----- |

**Unassigned on purpose:** PRs covered by a hold above.

## Ledger

| item | resolved_at (PT) | resolved by |
| ---- | ---------------- | ----------- |
