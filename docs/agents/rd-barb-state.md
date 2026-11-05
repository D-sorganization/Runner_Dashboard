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
| RD deploy/release HOLD                               | Dieter  | RD #2657   | `gh issue view 2657 -R D-sorganization/Runner_Dashboard --json state`           | 2026-11-04 16:04 | 1h  |
| #2658 disarmed                                       | Dieter  | RD #2658   | `gh pr view 2658 --json autoMergeRequest` is null                               | 2026-11-04 16:04 | 1h  |
| #2880 auto-merge off                                 | Dieter  | RD #2880   | `gh pr view 2880 --json autoMergeRequest` is null                               | 2026-11-04 16:04 | 1h  |
| GM #5540 auto-merge off until the hosted PDF passes  | Dieter  | GM #5540   | `gh pr view 5540 -R D-sorganization/Gasification_Model --json autoMergeRequest` | UNVERIFIED       | 1h  |
| GM 5535, 5542, 5548, 5549 unarmed until #5540 merges | Dieter  | GM #5540   | autoMergeRequest null on each                                                   | UNVERIFIED       | 1h  |
| **— Red mains —**                                    |         |            |                                                                                 |                  |     |
| **— Runner state —**                                 |         |            |                                                                                 |                  |     |
| Private minutes under 45,000 of 50,000 (RD is public; its CI doesn't count) | rd-barb | burn guard | billing usage for the month                                                     | UNVERIFIED       | 1h  |
| GM repo / GM PR checks return 404 to the fleet App at 16:04 PT — GM hold rows above left UNVERIFIED | rd-barb | #4039 pass | `gh api repos/D-sorganization/Gasification_Model` (404)                         | 2026-11-04 16:04 | 1h |
| RD #3863 not armed: state OPEN, autoMergeRequest null at 16:04 PT; no arm attempted | rd-barb | #4039 pass | `gh pr view 3863 --json mergeStateStatus,autoMergeRequest`                      | 2026-11-04 16:04 | 1h |
| Superseded RD #3833 still OPEN; replacement #3908 MERGED. Left open, not force-closed | rd-barb | #4039 pass | `gh pr view 3833 --json state,mergeStateStatus`; `gh pr view 3908 --json state`    | 2026-11-04 16:04 | 1h |
| RD #3794 still draft (superseded by merged #3993) — not this lane's claim; reaches 72h at ~20:19 PT today | rd-barb | #4039 pass | `gh pr view 3794 --json state,isDraft,createdAt`                                  | 2026-11-04 16:04 | 1h |
| RD #4032 (#3303 part 9) CLEAN, non-draft, no hold, merge_group queue 0 — NOT armed: cursor lane already has 6 armed in RD (#4017 #4018 #4021 #4034 #4035 #4036), over the 2/lane cap | rd-barb | 16:04 pass | `gh pr view 4032 --json mergeStateStatus,autoMergeRequest`                      | 2026-11-04 16:04 | 1h |
| RD #4026 (fix #4025 delegation) CI red: tests (3.11, 1, 1), tests, quality-gate — under 72h, not armed | rd-barb | 16:04 pass | `gh pr view 4026 --json statusCheckRollup`                                       | 2026-11-04 16:04 | 1h |
| Org billing usage returns 403 to the fleet App — burn-guard row stays UNVERIFIED; direct-notify alert not sent (send blocked in this unattended run's permission mode) | rd-barb | 16:04 pass | `gh api /organizations/D-sorganization/settings/billing/usage` (403)            | 2026-11-04 16:04 | 1h |
| **— Watching —**                                     |         |            |                                                                                 |                  |     |
| RD #2658 open >72h (cursor/rd-deploy-release-hold-307b), held by RD #2657 — no claim-gate action, hold not lifted | rd-barb | 72h-open-PR watch | `gh pr view 2658 -R D-sorganization/Runner_Dashboard --json createdAt` | 2026-11-04 16:04 | 24h |
| RD #2792 open >72h (bot/sync-fleet-agents, author app/d-sorg-jules) — Jules-owned sync PR, not a lane claim | rd-barb | 72h-open-PR watch | `gh pr view 2792 -R D-sorganization/Runner_Dashboard --json createdAt` | 2026-11-04 16:04 | 24h |
| RD #2852 open >72h (cursor/topology-sanitize-deploy-hold-a43e) — held, not touched per lane rules | rd-barb | 72h-open-PR watch | `gh pr view 2852 -R D-sorganization/Runner_Dashboard --json createdAt` | 2026-11-04 16:04 | 24h |
| RD #2880 open >72h (cursor/rd-fast-checks-runner-fc21) — auto-merge off per hold above | rd-barb | 72h-open-PR watch | `gh pr view 2880 -R D-sorganization/Runner_Dashboard --json createdAt` | 2026-11-04 16:04 | 24h |
| RD #3920 reported redundant by its own owner (superseded by merged #3918) — open, not closed by this lane (not our claim) | rd-barb | #3989 pre-flight 07:05 PT | `gh pr view 3920 --json state` (OPEN); `gh pr view 3918 --json state` (MERGED) | 2026-11-04 16:04 | 24h |
| GM #5540/#5535 merge condition — cannot re-verify GM PRs (404); hold not lifted by this lane | rd-barb | #4039 pass | `gh api repos/D-sorganization/Gasification_Model` (404)                          | UNVERIFIED       | 24h |
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
| RD #4006 agent worktree hygiene (Refs #2550) — merged; timer stays dry-run (`AGENT_WORKTREE_HYGIENE_APPLY=0`) | 2026-11-04 14:41 | PR #4006 merged |
| RD #3998 merge-queue pre-flight (fleet job board #2787) — merged | 2026-11-04 15:02 | PR merged |
| RD #3792 — issue CLOSED, superseded by merged #3993 | 2026-11-04 15:02 | PR #3993 merged |
| RD #4037 (#4012 fleet restic rollout) — merged | 2026-11-04 16:04 | PR merged |
