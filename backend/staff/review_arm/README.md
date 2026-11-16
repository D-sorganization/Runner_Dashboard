# Review/arm core — Refs #4249

This is the core slice of the approved RD-native pr-remediator service. It is
**not registered with the live scheduler** and cannot arm or dispatch without
explicitly supplied capabilities. There is no deploy, restart, or configuration
change in this slice (RD #2657 deployment hold remains in force).

`Settings.from_env(env, machine=...)` reads `RD_REVIEW_ARM_ENABLED` (default off),
`RD_REVIEW_ARM_DRY_RUN` (default true), `RD_REVIEW_ARM_INTERVAL_MINUTES` (default
10), `RD_REVIEW_ARM_REPOS`, and additional `RD_REVIEW_ARM_HELD_PRS` entries in
`Repository_Name#123` format. Only the machine named by the host-local
`RD_REVIEW_ARM_HOST` setting may enable the loop; missing/blank disables it.
Deployment must set this to the approved hub, never a worker (example: `hub-a`).
Seed holds are always retained. `Intake.candidates()` makes one source listing
per repository and confirms only candidates; source rate limiting aborts the
cycle. The source must budget and interpret GitHub primary/secondary rate-limit
headers through the existing GitHub client/governor.

`ReviewArmService.cycle()` is single-flight. Its `status()` returns last cycle,
skip/action counts and bounded escalations. Dry-run only reads and records
in-memory decisions. Disabled cycles perform no port calls. Live review/arm
decisions require a second complete snapshot at the same head before calling
the supplied capability. The arm capability must enforce the same head pin
atomically; the earlier snapshot alone is insufficient.

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
