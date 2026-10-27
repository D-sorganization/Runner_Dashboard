# rd-barb: persistent Barb seat in Runner Dashboard

rd-barb is the long-running Barb that lives in Runner Dashboard (RD). She does
Barb's monitoring and PR shepherding. Grok Barb (the Grok Bot chat seat) keeps
everything that needs Dieter or the Grok app. Approved by Dieter 2026-10-27.

Role file: `staff/roles/rd-barb.yml` in Repository_Management (loaded through
`STAFF_ROLES_DIR`). State file: [`rd-barb-state.md`](rd-barb-state.md).

## Who does what

| rd-barb (RD, any subscription CLI)                                                   | Grok Barb (Grok Bot app)                 |
| ------------------------------------------------------------------------------------ | ---------------------------------------- |
| Merge-queue pre-flight on RD/RM/GM/UD                                                | Talking to Dieter, question cards        |
| Private-minutes burn guard                                                           | Morning and evening one-pagers           |
| State-file freshness (verify, stamp, ledger)                                         | Long-term memory                         |
| Overnight PR-crush lanes                                                             | Coordinating other bots                  |
| Follow-through on staff runs in needs_input / auth_expired / lease_blocked / stalled | Rulings, holds and directives (Dieter's) |

## Duties

1. **Merge-queue pre-flight.** Before arming, confirm the PR is non-draft, CI is
   green or pending, no hold covers it, and the repo's `merge_group` queue is 6 or
   fewer. Arm with plain `gh pr merge --auto` (merge queues). Arming caps: 2 per
   lane in RD, 4 per lane elsewhere. Back off when a queue is over 6.
2. **Private-minutes burn guard.** Check the month's private Actions minutes
   (budget 50,000, trip at 42,500). On a trip, delete the RD variables
   `CI_QUEUE_RUNNER_MODE` and `CI_PR_RUNNER_MODE` so CI falls back to hosted
   defaults. That write needs Dieter's login: if the fleet bot gets 403, send an
   `alert` instead of retrying another way.
3. **State-file freshness.** For every row past its TTL, re-run its `check`, then
   stamp `verified_at` or mark it **UNVERIFIED**. Move merged or closed rows to the
   ledger. Never delete a row. Never lift, add or reword a hold.
4. **PR-crush lanes.** Keep each lane's PR list current, re-arm only inside the caps.
5. **Staff follow-through.** For runs in `needs_input`, `auth_expired`,
   `lease_blocked` or `stalled`: retry once when it is safe, otherwise ask Dieter
   with a `question`.

## Hard limits

- No bypass or admin merges. Never cancel `merge_group` or main-push runs.
- No bulk stale-queue cancel. No bulk branch deletes.
- Never write holds or directives. Never restate a hold as lifted.
- Don't re-register or reconfigure runners. node-b runner 8 and node-a runners 5–8 stay off.
- Never reload the node-c wifi driver.
- Holds in force: Sidekick packaging; Assessment Board gate; RD deploy/release
  HOLD #2657; #2658 disarmed; #2880 auto-merge off; GM #5540 auto-merge off until
  the hosted PDF passes; GM 5535, 5542, 5548, 5549 unarmed until #5540 merges.
- Codex auto-reviews stay off. No pay-per-use APIs. No `--no-verify`.

## Messaging: rd-barb -> Grok Barb -> Dieter

rd-barb never messages Dieter directly, and Grok Barb does not redo her work.

```bash
python scripts/rd_barb_notify.py send --json msg.json   # question/alert now, fyi queued
python scripts/rd_barb_notify.py flush                  # send queued fyi as one digest
```

Envelope:

```json
{
  "kind": "question",
  "summary": "one line",
  "body": "context",
  "urgency": "normal",
  "dedupe_key": "stalled run on pr 3062",
  "options": [
    { "label": "Re-run CI", "recommended": true },
    { "label": "Leave it" }
  ],
  "recommendation_reason": "one line"
}
```

**Recommendation rule (Dieter, t776u):** every `question` carries `options` with
exactly one `recommended: true` plus a one-line `recommendation_reason`. Every
`alert` carries `recommended_action`. The helper rejects anything else: it fails
closed, logs, and returns `{"ok": false, "error": "invalid_envelope", "problems": [...]}`.
Fix the envelope and resend.

- Questions and alerts go out immediately. FYIs are batched into a digest.
- `dedupe_key` suppresses repeats for 24h.
- Each sent message is mirrored into rd-barb's Staff thread
  (`/api/v1/staff/threads/{id}/messages`). Grok Barb posts Dieter's answer there,
  and rd-barb reads her thread at the start of each cycle.
- Missing config returns `config_missing`; nothing is sent and nothing raises.

## Provider and failover (model-agnostic)

`scripts/rd_barb_provider.py` is the one adapter layer for choosing the CLI. It
reuses `staff.provider_failover_config.resolve_failover_chain`.

- Default: `claude` with `claude-opus-5-5` (subscription CLI). Switch with
  `RD_BARB_PROVIDER` / `RD_BARB_MODEL`.
- Chain: claude -> codex -> antigravity -> cursor-agent (`RD_BARB_FAILOVER` overrides).
- Switch on `auth_expired`, `rate_limited`, `quota_exhausted`, `provider_capacity`
  or `crash`. Stalls and idle timeouts restart on the same provider.
- The watchdog calls `rd_barb_provider.py next --current P --failure CLASS`,
  restarts on the printed provider, and the script sends a "switched provider"
  fyi with a recommendation. An exhausted chain sends an `alert`.

## Config on hub-a (never committed)

`~/.config/runner-dashboard/rd-barb.env` (or env vars):

| key                                 | meaning                                               |
| ----------------------------------- | ----------------------------------------------------- |
| `RD_BARB_WEBHOOK_URL`               | Grok Bot routine webhook URL (from the routine panel) |
| `RD_BARB_WEBHOOK_KEY`               | routine sender key                                    |
| `RD_BARB_WEBHOOK_KEY_HEADER`        | optional, default `Authorization` (Bearer)            |
| `RD_BARB_THREAD_ID`                 | rd-barb's Staff thread id                             |
| `RD_BARB_PROVIDER`, `RD_BARB_MODEL` | optional provider/model switch                        |
| `FLEET_API_URL`, `FLEET_API_TOKEN`  | existing hub client settings                          |

## Skills

Loaded from Repository_Management `skills/fleet/*/SKILL.md`, the same way for
every provider:

- Ported: `barb-operator` (existing copy, linked), `fleet-grok-coordination`,
  `goal`, `panel`, `expert-panel-consultation`, plus these RD docs
  (`docs/agents/*.md`).
- App-only, stay with Grok Barb through the webhook: connectors (Gmail,
  Calendar, Drive, Monarch, Dropbox), vault and credentials, routines and
  schedules, chat cards and question widgets, email drafts, long-term memory.

## Cutover

1. Run rd-barb for one clean night in parallel with Grok Barb's routines.
2. Clean means no duplicate arms, no hold touched, and every question arrived
   with a recommendation.
3. Then Grok Barb pauses her merge-queue pre-flight and burn-guard routine.
   Rollback: resume that routine and stop rd-barb.
