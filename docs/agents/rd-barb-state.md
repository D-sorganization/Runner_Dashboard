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
| RD deploy/release HOLD                               | Dieter  | RD #2657   | `gh issue view 2657 -R D-sorganization/Runner_Dashboard --json state` (OPEN)    | 2026-11-04 19:33 | 1h  |
| #2658 disarmed                                       | Dieter  | RD #2658   | `gh pr view 2658 --json autoMergeRequest` is null                               | 2026-11-04 19:33 | 1h  |
| #2880 auto-merge off                                 | Dieter  | RD #2880   | `gh pr view 2880 --json autoMergeRequest` is null                               | 2026-11-04 19:33 | 1h  |
| GM #5540 now MERGED (hosted PDF passed); #5535 also MERGED. Hold condition satisfied — lifting it is Dieter's call, not recorded here | Dieter  | GM #5540   | `gh pr view 5540 -R D-sorganization/Gasification_Model --json autoMergeRequest,state` | UNVERIFIED (GM 404 to fleet App at 19:33 PT; last good 16:09) | 1h  |
| GM 5542, 5548, 5549 still OPEN, autoMergeRequest null (unarmed) | Dieter  | GM #5540   | autoMergeRequest null and state OPEN on each                                   | UNVERIFIED (GM 404 to fleet App at 19:33 PT; last good 16:09) | 1h  |
| **— Red mains —**                                    |         |            |                                                                                 |                  |     |
| **— Runner state —**                                 |         |            |                                                                                 |                  |     |
| Private minutes under 45,000 of 50,000 (RD is public; its CI doesn't count) | rd-barb | burn guard | billing usage for the month                                                     | UNVERIFIED       | 1h  |
| GM repo 404 to the fleet App at 19:33 PT (unchanged since 18:32). Alert dedupe `gm-404-fleet-app-20261104`; delivery still blocked from cloud worker session | rd-barb | 19:33 pass | `gh api repos/D-sorganization/Gasification_Model` (404)                         | UNVERIFIED       | 1h |
| RD Fleet API / barb_cli unreachable from cursor cloud worker at 19:33 PT (`http://127.0.0.1:8321` connection refused); RM #2539 `gh issue comment` returns repo 404 — status relay via PR comments only | rd-barb | 19:33 pass | `python3 scripts/barb_cli.py send …` / hub health                                 | 2026-11-04 19:33 | 1h |
| RD #3863 not armed: OPEN, mergeStateStatus DIRTY, autoMergeRequest null at 19:33 PT | rd-barb | 19:33 pass | `gh pr view 3863 --json mergeStateStatus,autoMergeRequest`                      | 2026-11-04 19:33 | 1h |
| Superseded RD #3833 still OPEN (DIRTY); replacement #3908 MERGED — not force-closed | rd-barb | 19:33 pass | `gh pr view 3833 --json state`; `gh pr view 3908 --json state`                  | 2026-11-04 19:33 | 1h |
| RD #3794 still draft (superseded by merged #3993) — not this lane's claim | rd-barb | 19:33 pass | `gh pr view 3794 --json state,isDraft`                                            | 2026-11-04 19:33 | 1h |
| RD #4032 (#3303 part 9) OPEN, mergeStateStatus BLOCKED, autoMergeRequest null — not armed (cursor lane already has 4 armed: #4017, #4018, #4021, #4035) | rd-barb | 19:33 pass | `gh pr view 4032 --json mergeStateStatus,autoMergeRequest`                        | 2026-11-04 19:33 | 1h |
| RD #4035 (#4010 node-c restic onboarding) OPEN, armed, anti-phantom guard green on latest push — cursor lane shepherding to merge | rd-barb | 19:33 pass | `gh pr view 4035 --json autoMergeRequest,mergeStateStatus`                        | 2026-11-04 19:33 | 1h |
| RD #4046 recovery interlock — **L5 owns slice 1** via PR #4054; cursor lane will not claim | rd-barb | Barb 19:36 PT | `gh pr view 4054 --json state`                                                    | 2026-11-04 19:33 | 1h |
| RD merge_group queue: 0 queued runs at 19:33 PT (under the 6 back-off) | rd-barb | 19:33 pass | `gh api "repos/D-sorganization/Runner_Dashboard/actions/runs?event=merge_group&status=queued"` total_count | 2026-11-04 19:33 | 1h |
| **— Watching —**                                     |         |            |                                                                                 |                  |     |
| RD #2658 open >72h (cursor/rd-deploy-release-hold-307b), held by RD #2657 — no claim-gate action | rd-barb | 72h-open-PR watch | `gh pr view 2658 -R D-sorganization/Runner_Dashboard --json createdAt` | 2026-11-04 19:33 | 24h |
| RD #2792 open >72h (bot/sync-fleet-agents, author app/d-sorg-jules) — Jules-owned sync PR | rd-barb | 72h-open-PR watch | `gh pr view 2792 -R D-sorganization/Runner_Dashboard --json createdAt` | 2026-11-04 19:33 | 24h |
| RD #2852 open >72h (cursor/topology-sanitize-deploy-hold-a43e) — held | rd-barb | 72h-open-PR watch | `gh pr view 2852 -R D-sorganization/Runner_Dashboard --json createdAt` | 2026-11-04 19:33 | 24h |
| RD #2880 open >72h (cursor/rd-fast-checks-runner-fc21) — auto-merge off per hold | rd-barb | 72h-open-PR watch | `gh pr view 2880 -R D-sorganization/Runner_Dashboard --json createdAt` | 2026-11-04 19:33 | 24h |
| RD #3920 redundant vs merged #3918 — open, not closed by this lane | rd-barb | 72h-open-PR watch | `gh pr view 3920 --json state`; `gh pr view 3918 --json state` | 2026-11-04 19:33 | 24h |
| **— Reminders —**                                    |         |            |                                                                                 |                  |     |
| 72h rule: RD #3794 (draft) crossed 2026-11-04 20:19 PT; #3832, #3833, #3842, #3843 cross ~2026-11-05 07:55 PT; **#3884** (cursor/issue-specs-claimability-e73d) crosses ~2026-11-05 12:17 PT — next pass: CI diagnosis or iterate-or-close | rd-barb | 72h-open-PR watch | `gh pr view <n> -R D-sorganization/Runner_Dashboard --json createdAt,state` | 2026-11-04 19:33 | 24h |

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
| RD #3998 (docs: fleet job board briefing and agent wiring, #2787) — merged | 2026-11-04 16:09 | PR #3998 merged |
| RD #3792 (superseded by #3993) — issue CLOSED, replacement PR #3993 MERGED | 2026-11-04 16:09 | PR #3993 merged |
| RD #4037 (#4012 fleet restic rollout) — merged | 2026-11-04 16:09 | PR #4037 merged |
| RD #4026 (#4025 forwarded on_behalf_of fix) — merged to main as 5037eeff | 2026-11-04 18:32 | PR #4026 merged |
