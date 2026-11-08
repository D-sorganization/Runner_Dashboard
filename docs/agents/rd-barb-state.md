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
| RD deploy/release HOLD                               | Dieter  | RD #2657   | `gh issue view 2657 -R D-sorganization/Runner_Dashboard --json state`           | 2026-11-08 00:01 | 1h  |
| #2658 disarmed                                       | Dieter  | RD #2658   | `gh pr view 2658 --json autoMergeRequest` is null                               | 2026-11-08 00:01 | 1h  |
| #2880 auto-merge off                                 | Dieter  | RD #2880   | `gh pr view 2880 --json autoMergeRequest` is null                               | 2026-11-08 00:01 | 1h  |
| GM #5540/#5535 MERGED (hosted PDF passed). Hold condition satisfied — lifting it is Dieter's call, not recorded here | Dieter  | GM #5540   | `gh pr view 5540 -R D-sorganization/Gasification_Model --json autoMergeRequest,state` | UNVERIFIED (GM repo 404 to fleet App at 2026-11-08 00:01; alert send denied in this session) | 1h  |
| **— Red mains —**                                    |         |            |                                                                                 |                  |     |
| **— Runner state —**                                 |         |            |                                                                                 |                  |     |
| Private minutes under 45,000 of 50,000 (RD is public; its CI doesn't count) | rd-barb | burn guard | billing usage for the month (org billing API 403 to fleet App at 2026-11-08 00:01) | UNVERIFIED       | 1h  |
| RD #3794 still draft (superseded by merged #3993) — not this lane's claim; past 72h open | rd-barb | 00:01 pass | `gh pr view 3794 --json state,isDraft,mergeStateStatus`                         | 2026-11-08 00:01 | 1h |
| RD merge_group queue: 0 queued runs at 00:01 PT (under the 6 back-off) | rd-barb | 00:01 pass | `gh api "repos/D-sorganization/Runner_Dashboard/actions/runs?event=merge_group&status=queued"` total_count | 2026-11-08 00:01 | 1h |
| **— Watching —**                                     |         |            |                                                                                 |                  |     |
| RD #2658 open >72h (cursor/rd-deploy-release-hold-307b), held by RD #2657 — no claim-gate action, hold not lifted | rd-barb | 72h-open-PR watch | `gh pr view 2658 -R D-sorganization/Runner_Dashboard --json createdAt` | 2026-11-08 00:01 | 24h |
| RD #2852 open >72h (cursor/topology-sanitize-deploy-hold-a43e) — held, not touched per lane rules | rd-barb | 72h-open-PR watch | `gh pr view 2852 -R D-sorganization/Runner_Dashboard --json createdAt` | 2026-11-08 00:01 | 24h |
| RD #2880 open >72h (cursor/rd-fast-checks-runner-fc21) — auto-merge off per hold above | rd-barb | 72h-open-PR watch | `gh pr view 2880 -R D-sorganization/Runner_Dashboard --json createdAt` | 2026-11-08 00:01 | 24h |
| **— Reminders —**                                    |         |            |                                                                                 |                  |     |
| 72h rule: RD #3794 (draft, codex lane, CONFLICTING) past 72h open — supersession by merged #3993 already diagnosed in-thread; close is the owning lane's call, not this lane's | rd-barb | 72h-open-PR watch | `gh pr view <n> -R D-sorganization/Runner_Dashboard --json createdAt,state` | 2026-11-08 00:01 | 24h |

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
| GM repo 404 to fleet App (was UNVERIFIED at 18:32) — confirmed reachable again (200) at 19:36 PT; GM #5540/#5535 MERGED, #5542/#5548/#5549 still OPEN unarmed, all re-verified | 2026-11-04 19:36 | repo API re-check |
| GM #5542, #5548, #5549 — all now CLOSED (not merged); no lane action, GM repo stayed reachable (200) through this pass | 2026-11-04 20:08 | state re-check |
| RD #4032 (#3303 part 9, cursor lane) — now MERGED; was open/BLOCKED at 19:36 PT | 2026-11-04 20:08 | state re-check |
| RD #2792 (bot/sync-fleet-agents, Jules-owned) — issue/PR now CLOSED; was open >72h watch row at 20:08 PT | 2026-11-04 20:35 | state re-check |
| RD #3920 (superseded by merged #3918) — now MERGED; was open/CLEAN watch row at 20:08 PT | 2026-11-04 20:35 | state re-check |
| RD #4017 (cursor lane) — now MERGED; was open/arm-cap row at 20:35 PT | 2026-11-04 21:36 | state re-check |
| GM #5542/#5548/#5549 — all confirmed CLOSED (not merged); already noted CLOSED at 20:08 PT, re-verified unchanged | 2026-11-04 21:36 | state re-check |
| RD #4059/#4060 (rd-barb freshness pass) — #4060 MERGED; superseded duplicate #4061 left open per no-close rule (see Watching row above) | 2026-11-04 21:36 | state re-check |
| RD #4062 (chore: reformat WSL export test with black) — now MERGED | 2026-11-04 21:36 | state re-check |
| RD #3832/#3843 (closed previously) — confirmed CLOSED, dropped from reminders row | 2026-11-04 21:36 | state re-check |
| RD #3833 (fix(agent-bus): defer cross-host claim reaping until readyz passes, #3823) — now MERGED; was open/BLOCKED superseded-by-#3908 watch row at 21:36 PT | 2026-11-04 22:05 | state re-check |
| RD #4066/#4067 (rd-barb freshness pass) — #4067 MERGED | 2026-11-04 22:05 | state re-check |
| RD #3863 — now MERGED; was open/CLEAN/not-armed runner-state row at 22:05 PT | 2026-11-04 22:38 | state re-check |
| RD #4063 (duplicate-PR issue, claude-a's claim), #4064 (author dieterolson personal) and #4065 (bot-authored) — issue #4063 CLOSED, #4064 MERGED, #4065 CLOSED (not merged); all were open/duplicate-PR rows at 22:05 PT | 2026-11-04 22:38 | state re-check |
| RD #4070/#4071 (rd-barb freshness pass) — #4071 MERGED | 2026-11-04 23:16 | state re-check |
| RD #4072/#4073 (rd-barb freshness pass) — #4073 MERGED | 2026-11-04 23:38 | state re-check |
| RD #3842 (fix(e2e): stabilize narrow staff tab hit-tests) — now MERGED; was open row referenced in #4074's reported deltas | 2026-11-04 23:38 | state re-check |
| RD #4074/#4075 (rd-barb freshness pass) — #4075 MERGED | 2026-11-05 00:19 | state re-check |
| RD #4076/#4077 (rd-barb freshness pass) — #4077 MERGED | 2026-11-05 00:19 | state re-check |
| RD #3884 (docs: desk lane issue specs, CLI label manifest, GitHub sync script) — MERGED; was runner-state row at 01:06 PT | 2026-11-06 07:19 | PR merged |
| RD #4051 (docs(rd-barb) 19:31 PT freshness #4049) — MERGED | 2026-11-06 09:05 | PR merged |
| RD #4018 (feat(deploy): fleet backup pilot restic client #4015) — MERGED | 2026-11-06 09:17 | PR merged |
| RD #4061 (#4059, `claude/4059-rd-barb-state-freshness`; was OPEN/CONFLICTING watch row at 11:42 PT 11-06) — CLOSED, not merged | 2026-11-06 12:36 | state re-check (00:01 PT 11-08) |
| RD #4127 (agy lane, spawn partial-launch recovery; was OPEN/red-CI runner-state row at 11:42 PT 11-06) — CLOSED, not merged | 2026-11-06 15:17 | state re-check (00:01 PT 11-08) |
