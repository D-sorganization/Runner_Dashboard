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
| RD deploy/release HOLD                               | Dieter  | RD #2657   | `gh issue view 2657 -R D-sorganization/Runner_Dashboard --json state`           | 2026-11-04 02:31 (open) | 1h  |
| #2658 disarmed                                       | Dieter  | RD #2658   | `gh pr view 2658 --json autoMergeRequest` is null                               | 2026-11-04 02:31 (null) | 1h  |
| #2880 auto-merge off                                 | Dieter  | RD #2880   | `gh pr view 2880 --json autoMergeRequest` is null                               | 2026-11-04 02:31 (null) | 1h  |
| GM #5540 auto-merge off until the hosted PDF passes  | Dieter  | GM #5540   | `gh pr view 5540 -R D-sorganization/Gasification_Model --json autoMergeRequest` | UNVERIFIED (RD App gets 404 on GM, 2026-11-04 02:31) | 1h  |
| GM 5535, 5542, 5548, 5549 unarmed until #5540 merges | Dieter  | GM #5540   | autoMergeRequest null on each                                                   | UNVERIFIED (RD App gets 404 on GM, 2026-11-04 02:31) | 1h  |
| **— Red mains —**                                    |         |            |                                                                                 |                  |     |
| RD main green at 4e222469 (Frontend Tests queued)    | rd-barb | RD Actions | `gh api repos/D-sorganization/Runner_Dashboard/actions/runs?branch=main&event=push` | 2026-11-04 02:31 | 1h  |
| **— Runner state —**                                 |         |            |                                                                                 |                  |     |
| Private minutes under 42,500 of 50,000               | rd-barb | burn guard | billing usage for the month (RD is public, not counted)                         | UNVERIFIED (no billing scope from staff worktree) | 1h  |
| Local RD API on :8321 reachable from rd-barb host    | rd-barb | staff API  | `curl -s http://127.0.0.1:8321/api/staff/summary`                               | UNVERIFIED (connection refused, 2026-11-04 02:31) | 1h  |
| **— Watching —**                                     |         |            |                                                                                 |                  |     |
| Fleet App can't arm auto-merge on RD (`enablePullRequestAutoMerge`: Resource not accessible by integration); #3845 CLEAN but unarmed | Dieter | automerge_guard | `python3 scripts/automerge_guard.py D-sorganization/Runner_Dashboard 3845 --arm` | 2026-11-04 02:31 | 24h |
| 72h: #2792 (fleet AGENTS sync, opened 10-25): DIRTY plus red tsc, Vitest, frontend-required. Main already carries the synced fleet core, so this PR looks superseded. Decision: close via the redundant-PR closer | bot     | RD #2792   | `gh pr view 2792 --json mergeStateStatus,statusCheckRollup`                     | 2026-11-04 02:31 | 24h |
| 72h: #2852 (opened 10-25): held under #2657, `do-not-merge`, DIRTY. No action until the hold clears | Dieter  | RD #2852   | label `do-not-merge` present                                                    | 2026-11-04 02:31 | 24h |
| Reaching 72h around 11-05 08:00 PT, red CI: #3843 (tests, quality-gate), #3842 (SPEC freshness, Staff e2e, frontend), #3853 (tests, Staff e2e, frontend) | cursor  | RD Actions | `gh pr view N --json statusCheckRollup`                                         | 2026-11-04 02:31 | 24h |
| Reaching 72h around 11-05, DIRTY (needs rebase for conflicts): #3832, #3833, #3863, #3884 | cursor  | RD PRs     | `gh pr view N --json mergeStateStatus`                                          | 2026-11-04 02:31 | 24h |
| **— Reminders —**                                    |         |            |                                                                                 |                  |     |

## PR-crush lanes

Caps: 2 armed per lane in RD, 4 elsewhere. Back off when a repo's `merge_group` queue is over 6.

| lane | host | tool | repo | PRs | log | start |
| ---- | ---- | ---- | ---- | --- | --- | ----- |

**Unassigned on purpose:** PRs covered by a hold above.

## Ledger

| item | resolved_at (PT) | resolved by |
| ---- | ---------------- | ----------- |
