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
| RD deploy/release HOLD                               | Dieter  | RD #2657   | `gh issue view 2657 -R D-sorganization/Runner_Dashboard --json state`           | 2026-11-04 14:01 | 1h  |
| #2658 disarmed                                       | Dieter  | RD #2658   | `gh pr view 2658 --json autoMergeRequest` is null                               | 2026-11-04 14:01 | 1h  |
| #2880 auto-merge off                                 | Dieter  | RD #2880   | `gh pr view 2880 --json autoMergeRequest` is null                               | 2026-11-04 14:01 | 1h  |
| GM #5540 auto-merge off until the hosted PDF passes  | Dieter  | GM #5540   | `gh pr view 5540 -R D-sorganization/Gasification_Model --json autoMergeRequest` | UNVERIFIED       | 1h  |
| GM 5535, 5542, 5548, 5549 unarmed until #5540 merges | Dieter  | GM #5540   | autoMergeRequest null on each                                                   | UNVERIFIED       | 1h  |
| **— Red mains —**                                    |         |            |                                                                                 |                  |     |
| **— Runner state —**                                 |         |            |                                                                                 |                  |     |
| Private minutes under 45,000 of 50,000 (RD is public; its CI doesn't count) | rd-barb | burn guard | billing usage for the month                                                     | UNVERIFIED       | 1h  |
| GM repo access regressed at 14:01 PT: `gh pr view 5540 -R D-sorganization/Gasification_Model` returns "Could not resolve to a Repository" for the fleet App (it resolved at 13:33 PT) — GM hold rows above marked UNVERIFIED, holds unchanged. Org billing usage still 403 | rd-barb | 14:01 PT pass | `gh pr view 5540 -R D-sorganization/Gasification_Model --json state,autoMergeRequest` | 2026-11-04 14:01 | 1h |
| RD #3863 not armed: mergeStateStatus DIRTY at 14:01 PT, autoMergeRequest null; no arm attempted | rd-barb | 14:01 PT pass | `gh pr view 3863 --json mergeStateStatus,autoMergeRequest` | 2026-11-04 14:01 | 1h |
| Superseded RD #3833 still OPEN (mergeStateStatus DIRTY at 14:01 PT); replacement #3908 MERGED. Closing failed App 403 on `addComment` earlier; left open, not force-closed | rd-barb | 14:01 PT pass | `gh pr view 3833 --json state,mergeStateStatus`; `gh pr view 3908 --json state` | 2026-11-04 14:01 | 1h |
| RD #4004 passes merge-queue pre-flight at 14:01 PT (non-draft, CLEAN, 0 failing/0 pending checks, no hold label, merge_group in progress 4 <= 6) but is unarmed; this pass's session could not run `automerge_guard.py --arm` (tool permission), so it was left for the next pass or its cursor lane. UI change: PR has no 1280/390 screenshots yet | rd-barb | 14:01 PT pass | `gh pr view 4004 --json mergeStateStatus,autoMergeRequest,statusCheckRollup` | 2026-11-04 14:01 | 1h |
| **— Watching —**                                     |         |            |                                                                                 |                  |     |
| RD #2658 open >72h (cursor/rd-deploy-release-hold-307b), held by RD #2657 — no claim-gate action, hold not lifted | rd-barb | 72h-open-PR watch | `gh pr view 2658 -R D-sorganization/Runner_Dashboard --json createdAt` | 2026-11-04 14:01 | 24h |
| RD #2792 open >72h (bot/sync-fleet-agents, author app/d-sorg-jules) — Jules-owned sync PR, not a lane claim | rd-barb | 72h-open-PR watch | `gh pr view 2792 -R D-sorganization/Runner_Dashboard --json createdAt` | 2026-11-04 14:01 | 24h |
| RD #2852 open >72h (cursor/topology-sanitize-deploy-hold-a43e) — held, not touched per lane rules | rd-barb | 72h-open-PR watch | `gh pr view 2852 -R D-sorganization/Runner_Dashboard --json createdAt` | 2026-11-04 14:01 | 24h |
| RD #2880 open >72h (cursor/rd-fast-checks-runner-fc21) — auto-merge off per hold above | rd-barb | 72h-open-PR watch | `gh pr view 2880 -R D-sorganization/Runner_Dashboard --json createdAt` | 2026-11-04 14:01 | 24h |
| RD #3920 reported redundant by its own owner (superseded by merged #3918) — open, not closed by this lane (not our claim) | rd-barb | #3989 pre-flight 07:05 PT | `gh pr view 3920 --json state` (OPEN); `gh pr view 3918 --json state` (MERGED) | 2026-11-04 14:01 | 24h |
| GM #5540 and #5535 both now show MERGED — the "unarmed until #5540 merges" condition on 5542/5548/5549 above appears met; flagging for Dieter/Barb to decide on lifting that hold explicitly, not lifted by this lane | rd-barb | #4001 10:40 PT pass | `gh pr view 5540,5535 -R D-sorganization/Gasification_Model --json state` (both MERGED) | 2026-11-04 13:33 | 24h |
| RD #3792 resolved: issue now CLOSED — draft PR #3794 superseded by merged #3993 ("fix(staff): report failed shadow-only thread delivery"); no further action needed. Codex draft #3794 is still OPEN at 14:01 PT (superseded, not our claim; left for its owner or the redundant-PR closer) | rd-barb | #4029 13:33 PT pass | `gh issue view 3792 --json state` (CLOSED); `gh pr view 3993 --json state,title` (MERGED); `gh pr view 3794 --json state,isDraft` | 2026-11-04 14:01 | 24h |
| **— Reminders —**                                    |         |            |                                                                                 |                  |     |

## PR-crush lanes

Caps: 2 armed per lane in RD, 4 elsewhere. Back off when a repo's `merge_group` queue is over 6.

| lane | host | tool | repo | PRs | log | start |
| ---- | ---- | ---- | ---- | --- | --- | ----- |

**Unassigned on purpose:** PRs covered by a hold above.

## Ledger

| item | resolved_at (PT) | resolved by |
| ---- | ---------------- | ----------- |
| RD #2793 (CI: detect Docker PR scope via git diff instead of PR files API) — landed (board #5) | 2026-10-25 16:49 | PR merged |
| RD #2810 (deploy: fail-closed fleet-gh GitHub App gh/git wrappers) — landed (board #5) | 2026-10-25 21:01 | PR merged |
| RD #2811 (CI: ignore wc -l total row in PR line-cap check) — landed (board #5) | 2026-10-25 16:32 | PR merged |
