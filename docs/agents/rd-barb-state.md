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
| RD deploy/release HOLD                               | Dieter  | RD #2657   | `gh issue view 2657 -R D-sorganization/Runner_Dashboard --json state`           | 2026-11-04 19:33 | 1h  |
| #2658 disarmed                                       | Dieter  | RD #2658   | `gh pr view 2658 --json autoMergeRequest` is null                               | 2026-11-04 19:33 | 1h  |
| #2880 auto-merge off                                 | Dieter  | RD #2880   | `gh pr view 2880 --json autoMergeRequest` is null                               | 2026-11-04 19:33 | 1h  |
| GM #5540 now MERGED (hosted PDF passed); #5535 also MERGED. Hold condition satisfied — lifting it is Dieter's call, not recorded here | Dieter  | GM #5540   | `gh pr view 5540 -R D-sorganization/Gasification_Model --json autoMergeRequest,state` | UNVERIFIED (GM still 404 to fleet App at 19:33 PT; last good 16:09) | 1h  |
| GM 5542, 5548, 5549 still OPEN, autoMergeRequest null (unarmed) | Dieter  | GM #5540   | autoMergeRequest null and state OPEN on each                                   | UNVERIFIED (GM still 404 to fleet App at 19:33 PT; last good 16:09) | 1h  |
| **— Red mains —**                                    |         |            |                                                                                 |                  |     |
| **— Runner state —**                                 |         |            |                                                                                 |                  |     |
| Private minutes under 45,000 of 50,000 (RD is public; its CI doesn't count) | rd-barb | burn guard | billing usage for the month                                                     | UNVERIFIED       | 1h  |
| GM repo 404 to the fleet App at 18:32 and again at 19:33 PT (was 200 at 16:09). Alert (dedupe `gm-404-fleet-app-20261104`) still NOT delivered: direct-notify was blocked by session permissions in both passes | rd-barb | 19:33 pass | `gh api repos/D-sorganization/Gasification_Model` (404)                         | UNVERIFIED       | 1h |
| Local RD API `127.0.0.1:8321/api/staff/summary` unreachable from this worker at 19:33 PT (curl HTTP 000); staff board/runs not checked this pass, so needs_input/stalled staff runs are unverified. Alert not delivered (direct-notify blocked by session permissions) | rd-barb | 19:33 pass | `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8321/api/staff/summary` | UNVERIFIED | 1h |
| RD #3863 not armed: state OPEN, mergeStateStatus DIRTY, autoMergeRequest null at 19:33 PT; no arm attempted | rd-barb | 19:33 pass | `gh pr view 3863 --json mergeStateStatus,autoMergeRequest`                      | 2026-11-04 19:33 | 1h |
| Superseded RD #3833 still OPEN (DIRTY); replacement #3908 MERGED. Left open, not force-closed | rd-barb | 18:32 pass | `gh pr view 3833 --json state,mergeStateStatus`; `gh pr view 3908 --json state`    | 2026-11-04 19:33 | 1h |
| RD #3794 still draft (DIRTY; superseded by merged #3993) — not this lane's claim | rd-barb | 18:32 pass | `gh pr view 3794 --json state,isDraft`                                            | 2026-11-04 19:33 | 1h |
| RD #4032 (#3303 part 9, cursor/3303-go-public-runners-part9-7724) open, mergeStateStatus CLEAN, autoMergeRequest null — not armed: cursor lane already has 6 armed (#4017, #4018, #4021, #4034, #4035, #4036), over the 2/lane cap; not this lane's claim | rd-barb | 18:32 pass | `gh pr view 4032 --json state,mergeStateStatus,autoMergeRequest,headRefName`    | 2026-11-04 19:33 | 1h |
| RD merge_group queue: 0 queued runs at 19:33 PT (under the 6 back-off) | rd-barb | 19:33 pass | `gh api "repos/D-sorganization/Runner_Dashboard/actions/runs?event=merge_group&status=queued"` total_count | 2026-11-04 19:33 | 1h |
| **— Watching —**                                     |         |            |                                                                                 |                  |     |
| RD #2658 open >72h (cursor/rd-deploy-release-hold-307b), held by RD #2657 — no claim-gate action, hold not lifted | rd-barb | 72h-open-PR watch | `gh pr view 2658 -R D-sorganization/Runner_Dashboard --json createdAt` | 2026-11-04 19:33 | 24h |
| RD #2792 open >72h (bot/sync-fleet-agents, author app/d-sorg-jules) — Jules-owned sync PR, not a lane claim | rd-barb | 72h-open-PR watch | `gh pr view 2792 -R D-sorganization/Runner_Dashboard --json createdAt` | 2026-11-04 19:33 | 24h |
| RD #2852 open >72h (cursor/topology-sanitize-deploy-hold-a43e) — held, not touched per lane rules | rd-barb | 72h-open-PR watch | `gh pr view 2852 -R D-sorganization/Runner_Dashboard --json createdAt` | 2026-11-04 19:33 | 24h |
| RD #2880 open >72h (cursor/rd-fast-checks-runner-fc21) — auto-merge off per hold above | rd-barb | 72h-open-PR watch | `gh pr view 2880 -R D-sorganization/Runner_Dashboard --json createdAt` | 2026-11-04 19:33 | 24h |
| RD #3920 reported redundant by its own owner (superseded by merged #3918) — open, not closed by this lane (not our claim) | rd-barb | #3989 pre-flight 07:05 PT | `gh pr view 3920 --json state` (OPEN); `gh pr view 3918 --json state` (MERGED) | 2026-11-04 19:33 | 24h |
| **— Reminders —**                                    |         |            |                                                                                 |                  |     |
| 72h rule: RD #3794 (draft, codex lane) crosses 72h open at ~2026-11-04 20:19 PT; #3832, #3833, #3842, #3843 cross ~2026-11-05 07:55 PT; #3884 (cursor/issue-specs-claimability-e73d, DIRTY) crosses ~2026-11-05 12:17 PT — next pass gives each a CI diagnosis or iterate-or-close note | rd-barb | 72h-open-PR watch | `gh pr view <n> -R D-sorganization/Runner_Dashboard --json createdAt,state` | 2026-11-04 19:33 | 24h |

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
| RD #4006 agent worktree hygiene (Refs #2550) — merged; timer stays dry-run (`AGENT_WORKTREE_HYGIENE_APPLY=0`) | 2026-11-04 14:41 | PR #4006 merged |
| RD #3998 (docs: fleet job board briefing and agent wiring, #2787) — merged, autoMergeRequest null | 2026-11-04 16:09 | PR #3998 merged |
| RD #3792 (superseded by #3993) — issue CLOSED, replacement PR #3993 MERGED | 2026-11-04 16:09 | PR #3993 merged |
| RD #4037 (#4012 fleet restic rollout, cursor lane shepherd) — merged, autoMergeRequest null | 2026-11-04 16:09 | PR #4037 merged |
| RD #4026 (#4025 forwarded on_behalf_of fix; was red CI at 16:09) — merged to main as 5037eeff | 2026-11-04 18:32 | PR #4026 merged |
