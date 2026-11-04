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
| RD deploy/release HOLD                               | Dieter  | RD #2657   | `gh issue view 2657 -R D-sorganization/Runner_Dashboard --json state`           | 2026-11-04 03:01 (OPEN) | 1h  |
| #2658 disarmed                                       | Dieter  | RD #2658   | `gh pr view 2658 --json autoMergeRequest` is null                               | 2026-11-04 03:01 (null) | 1h  |
| #2880 auto-merge off                                 | Dieter  | RD #2880   | `gh pr view 2880 --json autoMergeRequest` is null                               | 2026-11-04 03:01 (null) | 1h  |
| GM #5540 auto-merge off until the hosted PDF passes  | Dieter  | GM #5540   | `gh pr view 5540 -R D-sorganization/Gasification_Model --json autoMergeRequest` | UNVERIFIED (2026-11-04 03:01: repo returns 404 to the fleet App) | 1h  |
| GM 5535, 5542, 5548, 5549 unarmed until #5540 merges | Dieter  | GM #5540   | autoMergeRequest null on each                                                   | UNVERIFIED (2026-11-04 03:01: repo returns 404 to the fleet App) | 1h  |
| **— Red mains —**                                    |         |            |                                                                                 |                  |     |
| **— Runner state —**                                 |         |            |                                                                                 |                  |     |
| Private minutes under 42,500 of 50,000               | rd-barb | burn guard | billing usage for the month                                                     | UNVERIFIED (2026-11-04 03:01: local RD API unreachable, billing not readable by App) | 1h  |
| **— Watching —**                                     |         |            |                                                                                 |                  |     |
| RD #2792 open >72h (fleet AGENTS.md sync): CONFLICTING; tsc, Vitest, frontend-required red. Recommend close as superseded by later syncs (redundant-PR closer) | bot | 72h rule | `gh pr view 2792 --json mergeable,statusCheckRollup` | 2026-11-04 03:01 | 24h |
| RD #2852 open >72h (topology sanitize, `do-not-merge`, held): CONFLICTING; docker-build-scan, frontend-required red. Waits on Dieter | cursor | 72h rule | `gh pr view 2852 --json mergeable,labels` | 2026-11-04 03:01 | 24h |
| RD #3845 MERGEABLE CLEAN, all checks green, unarmed. Arm next pass (this pass's arm was blocked by session permissions) | cursor | pre-flight | `gh pr view 3845 --json mergeStateStatus,autoMergeRequest` | 2026-11-04 03:01 | 24h |
| RD #3920, #3897, #3884, #3863, #3833, #3832 CONFLICTING (need rebase by owning lane); #3853, #3843, #3842, #3794 BLOCKED on red required checks | lanes | pre-flight | `gh pr list --json mergeStateStatus` | 2026-11-04 03:01 | 24h |
| **— Reminders —**                                    |         |            |                                                                                 |                  |     |

## PR-crush lanes

Caps: 2 armed per lane in RD, 4 elsewhere. Back off when a repo's `merge_group` queue is over 6.

| lane | host | tool | repo | PRs | log | start |
| ---- | ---- | ---- | ---- | --- | --- | ----- |

**Unassigned on purpose:** PRs covered by a hold above.

## Ledger

| item | resolved_at (PT) | resolved by |
| ---- | ---------------- | ----------- |
