# Review/arm service — Refs #4249

The approved RD-native pr-remediator service: it replaces the box scripts
(intake.py, driver.py, armd.py, review_ingest.py, relay.py). It is **default
off** and **dry-run by default**. Deployment (and the 48 h dry run) is a
separate operator step; this code performs no deploy, restart or service
enablement.

## Enabling and configuration (host-local env)

| Variable | Default | Meaning |
| --- | --- | --- |
| `RD_REVIEW_ARM_HOST` | unset (off) | Enable flag: only the machine with this name runs the loop. |
| `RD_REVIEW_ARM_ENABLED` | unset | `0`/`false` is a kill switch that wins over the host. |
| `RD_REVIEW_ARM_DRY_RUN` | true | Only `0`/`false`/`no`/`off` leaves dry-run. |
| `RD_REVIEW_ARM_INTERVAL_MINUTES` | 10 | Cycle interval (1..1440). |
| `RD_REVIEW_ARM_MAX_DISPATCH` | 5 | Review dispatches per cycle (0..60); the rest stay queued. |
| `RD_REVIEW_ARM_STALL_MINUTES` | 60 | Stall alert threshold. |
| `RD_REVIEW_ARM_REPOS` | fleet repos | Comma-separated repository names. |
| `RD_REVIEW_ARM_HELD_PRS` | — | Extra holds, `Repo#123`; seed holds always apply. |
| `RD_REVIEW_ARM_LEDGER` | `~/.config/runner-dashboard/review-arm-ledger.sqlite3` | Durable ledger. |
| `RD_REVIEW_ARM_BOX_STATE_DIR` | — | Directory with the box's `arm_holds.txt`, `blocked_heads.txt`, `arm_override.txt`; imported once. |

`start_review_arm` runs from the leader's staff-scheduler bootstrap after the
startup reconcile. `GET /api/staff/review-arm/status` (staff.read) reports
enabled, dry_run, host, queue depth, last intake/dispatch/verdict/arm times,
counts, escalations and recent decisions.

## Cycle

1. **Verdict phase** (armd.py / review_ingest.py): for each dispatched review
   not yet handled, read the run outcome; after a failed post-run step or a hub
   restart, recover the `STAFF_RESULT` verdict from the run events. Changes with
   BLOCKING findings (or escalate) block the head until a new head or an
   operator override; round >= 2 still blocking escalates to the owner. An
   effective approve (policy 2: changes with zero BLOCKING) goes through
   `arm_gate` (holds, open, draft, blocked, head moved) and the arm-only
   `GitHubArmer`. The reviewer's final message is relayed to the PR (relay.py;
   never `work_private`).
2. **Intake** (intake.py): one GraphQL list per repo; required checks confirmed
   at the head SHA; held / hold-labelled / blocked / armed / queued / reviewed
   PRs skipped; more than four rounds escalates to the owner instead of
   re-dispatching; otherwise dispatch a code-reviewer run through
   `dispatch_staff_run` (independent provider family, strict provider,
   code-read-only).
3. **Stall alert**: queue non-empty and no dispatch or arm for
   `RD_REVIEW_ARM_STALL_MINUTES` raises one alert per episode (fleet event +
   rd-barb alert).

Dry-run decides and logs everything (status shows would-dispatch / would-arm)
but performs no GitHub write, no dispatch and no owner notification.

## Arm-only

`armer.py` can send exactly one mutation: `enablePullRequestAutoMerge`
(SQUASH, `expectedHeadOid` = reviewed head). There is no merge, enqueue,
admin or REST-merge path. Before arming it re-reads the PR and refuses a moved
head, a closed/draft PR, a hold, incomplete labels and unacknowledged file
deletions (RD #4449; `deletions-acknowledged` label or body line).
`scripts/automerge_guard.arm_auto_merge` no longer falls back to a direct
merge or enqueue either.

## Evidence bar and parity notes

An approve needs some review evidence; a non-blank review body suffices (box
parity), an empty rubber-stamp approve escalates. `tests/test_staff_review_arm_replay.py`
replays the box's last 7 days and requires identical arm decisions.

## Original design notes (core slice)

Boundary requirements for the subsequent integration slice:

- Supply complete labels and holds (including C3/MS-14 and Sidekick packaging);
  missing/partial hold data is unavailable. Source failures never mean no holds.
- List open PRs once per repo, then confirm required checks at the same full SHA
  for candidates. Never infer required checks solely from mergeStateStatus or an
  aggregate rollup. Pagination/truncation must be made visible, never treated as
  absence of holds or prior runs.
- Use the existing RunStore's unbounded `list_runs_for_target` for all prior
  code-reviewer runs, including retries, to compute the round. Use exact-head
  run/prompt evidence for queued/reviewed dedupe; a new SHA is a new obligation.
- Bind `Ledger.ingest(Verdict(...))` to the trusted staff run context and verify
  reviewer identity/model, author family, and diff-anchored findings/tests there.
  Ledger rejects blank evidence; it does not inspect a repository diff itself.
  Do not accept ledger input or operator overrides from unauthenticated HTTP.
- Dispatch via `dispatch_staff_run` so existing review buckets, reservations,
  audit, budget, holds and provider health checks remain authoritative. Set
  strict provider and code-read-only authority. Only Codex and Claude are
  selectable here, with a different author provider family; no Cursor/Jules or
  sensitive-repo Gemini path. Codex depends on the read-only sandbox interface
  from open RD #4483; do not copy its adapter changes.
- Keep review selection and submit under the existing shared review claim lock;
  recheck exact-head dedupe there to prevent duplicate submissions across workers.
- Reuse shepherd hold/arm admission and WIP caps. The existing `arm_auto_merge`
  currently has clean-PR direct-merge/enqueue fallbacks; do not connect it until
  an arm-only path exists. An arm call must pin the full current SHA, request
  squash auto-merge, and never directly merge, enqueue, or bypass protection.
- Persist/deduplicate round-cap escalations and route them to Barb. Never post
  PR comments on work_private. The core itself posts no PR comments anywhere.
- Add approved-hub scheduler leader fencing and a read-only staff status route
  around this core; call at `interval_seconds`. No feature flag should enable
  a partial port implementation.

The append-only ledger retains a BLOCKING changes/escalate verdict at that head
even after a subsequent approve. A new head has independent state. An explicit
operator identity/reason override resets the block but does not supply approval.
Policy 2 maps changes with zero blocking findings to approve only when both
observations and tests exist. Rubber stamps become escalations.
