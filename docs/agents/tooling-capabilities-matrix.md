# Fleet Tooling Capabilities Matrix, Bean-Counter Ledger, Agent Report Cards & Multi-Barb Protocol

Synthesized with the 3-Seat Expert Panel (`th_5e95506efbf0`: Astra/`codex`, Argon/`antigravity`, Fable/`claude`).

## 1. Specialist Role: `tooling-scout` — CLI vs. Desktop App Capability Matrix

| Agent / Tool                                         | Headless CLI Adapter                                                                 | Desktop / GUI Surface                                                           | Best-Fit Workload Tier                                                                           | Key Strengths & Constraints                                                                                                                          |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Claude Code / Desktop** (`claude`)                 | `claude -p --output-format stream-json --verbose` (`CLAUDE_CONFIG_DIR=~/.claude-rd`) | Claude Desktop + MCP + Computer Use (`~/.claude/skills/barb-operator/SKILL.md`) | **Complex & Standard** (`chief-architect`, `issue-remediator`, `pr-remediator`, `code-reviewer`) | Highest first-pass yield on multi-file refactors and PR remediation. Keep `~/.claude` and `~/.claude-rd` credentials synced across all 4 nodes.      |
| **Codex CLI / Desktop** (`codex`)                    | `codex exec --json --full-auto`                                                      | Codex Desktop (`~/.codex/skills/barb-operator/SKILL.md`)                        | **Complex & Standard** (`pragmatic-programmer`, `test-hygiene`, `issue-remediator`)              | Strong deterministic verification and low input-to-output token overhead.                                                                            |
| **Antigravity / Gemini CLI** (`antigravity` / `agy`) | `gemini -p --output-format stream-json --yolo`                                       | Gemini / Antigravity Desktop (`~/.gemini/skills/barb-operator/SKILL.md`)        | **Routine / Read-Only Triage** (`night-watch`, `tooling-scout`, `bean-counter`, `sanitation`)    | Fast context scanning; avoid routing complex multi-file `issue-remediator` tasks to `gemini-3.8-flash-high` where empirical first-pass yield is low. |
| **Cursor Agent / Desktop** (`cursor-barb`)           | Cursor CLI / Local Exec                                                              | Cursor Desktop + Browser / Canvas (`~/.cursor/skills/barb-operator/SKILL.md`)   | **Interactive GUI / Browser / OAuth / Visual Inspection**                                        | Ideal for `needs_desktop` handoffs requiring browser SSO/OAuth login, visual layout verification, or interactive Canvas authoring.                   |
| **Grok Bot** (`grok-barb`)                           | Executor + Fleet SSH/WSL Orchestration                                               | Box Browser + Multi-Machine Local Exec                                          | **Fleet-Wide Barb Coordinator & Cross-Node Steward**                                             | Coordinates all 4 nodes (`hub-a`, `node-a`, `node-b`, `node-c`), dispatches panels, and audits turnover compliance.                                  |

## 2. CLI <-> Desktop App Collaborative Handoff Protocol

`surface` and `handoff_state` are different columns. `classify_task_surface` (`POST /api/v1/staff/barb/classify-surface`, scope `staff.read`) returns `surface` as `headless_cli`, `desktop_app`, or `hybrid_handoff`. `POST /api/v1/staff/barb/inventory` (`staff.dispatch`) stores that value only when the body `surface` is one of those three; otherwise it keeps the stored surface. A handoff transition never writes `surface`, so desktop completion leaves it unchanged.

`handoff_state` is `none`, `needs_desktop`, `desktop_claimed`, `resume_ready`, or `completed`. `status` is a separate column. `POST /api/v1/staff/barb/handoff` (`staff.dispatch`) requires `task_id`, `action`, `actor`, and `expected_epoch` (the task inventory fencing token). Optional fields include `persona`, `reason`, `artifact_uri`, and `resume_notes`. The stored owner is `persona` when set, otherwise `actor`. Each accepted transition increments `fencing_epoch` by one. A mismatched `expected_epoch` returns 409 `stale_fencing_epoch`.

| `action`           | `handoff_state`   | `status`                                                                                |
| ------------------ | ----------------- | --------------------------------------------------------------------------------------- |
| `request_desktop`  | `needs_desktop`   | `blocked_desktop`                                                                       |
| `claim_desktop`    | `desktop_claimed` | `in_desktop`                                                                            |
| `complete_desktop` | `resume_ready`    | `in_progress` (`artifact_uri` and `resume_notes` are stored; `surface` stays unchanged) |
| `resume_cli`       | `none`            | unchanged (still `in_progress` when the previous action was `complete_desktop`)         |
| `complete_task`    | `completed`       | `completed`                                                                             |

Barb lease fencing is separate from that task `fencing_epoch`. A Barb-seat write sends `X-Barb-Scope` and `X-Barb-Lease-Epoch`, and may send `X-Barb-Holder` to name one seat under a shared token. In the default enforce mode, a Barb-seat handoff, inventory, or turnover write without those two headers returns 409 `claim_required`. Sending only one of scope or epoch returns 422. A non-Barb caller may omit them.

`GET /api/v1/staff/barb/inventory` (`staff.read`) applies its `surface` query parameter to `handoff_state`, not the `surface` column.

Live read probe 2026-11-25 with the fleet cursor agent token: `GET` report-cards, leaderboard, bean-counter, and barb inventory returned HTTP 200. `GET /api/coordination/claims` for Runner_Dashboard#4667 returned HTTP 503 in that pass. Handoff `POST` was not called.

## 3. Specialist Role: `bean-counter` & Zero-LLM-Cost Agent Report Cards

Exposed at `GET /api/v1/staff/report-cards`, `GET /api/v1/staff/leaderboard`, `GET /api/v1/staff/bean-counter` (and `/api/staff/report-cards`, `/api/staff/leaderboard`, `/api/staff/bean-counter`):

- **First-Pass Yield (`first_pass_success_rate` + `first_pass_ci_95`):** Proportion of terminal runs that succeed on attempt 1 with `exit_code == 0`, verified output, and no subsequent `pr-remediator` follow-up run on the same PR/branch, accompanied by a 95% Wilson confidence interval `[low, high]`.
- **Bug / Rework Rate (`rework_rate` & `remediation_followups_per_pr`):** Fraction of runs that fail, retry, or require `pr-remediator` follow-up.
- **Effective Token Cost per Deliverable (`effective_tokens_per_success`, `effective_tokens_per_first_pass`, `effective_tokens_per_merge`):** Total tokens across all attempts, failed runs, and retries divided by successful/merged deliverables—exposing when a lower-tier model costs more in the long run than a higher-tier model up front.
- **Diagnostic Flags:** `zero_yield_token_sink`, `tier_mismatch_waste`, `unverified_output_churn`, `high_rework_rate`, `excessive_input_context`, `low_sample_size`.

## 4. Multi-Barb Parallel Persona & Turnover Compliance

Any agent (`grok-barb`, `codex-barb`, `claude-barb`, `agy-barb`, `cursor-barb`, `rd-barb`) can check out a scoped sub-role in parallel via `POST /api/v1/staff/barb/leases/checkout`:

- `barb:front-door`
- `barb:ci-shepherd`
- `barb:pr-queue`
- `barb:dispatch-coordinator`
- `barb:issue-triage`
- `barb:night-watch`

Releasing a lease (`POST /api/v1/staff/barb/leases/release`) enforces a **turnover compliance check**: the holder must have logged a turnover entry during the shift (`POST /api/v1/staff/barb/turnover`) or provide `turnover_summary` inline on release.

**Seat security (#2489).** The hub is the single writer and seats are advisory identities:

- `holder` is derived from the authenticated caller id. A body `holder` is optional; it must equal the caller id or `<caller-id>:<suffix>` (to tell seats apart under the shared fleet token), otherwise 403. This applies to checkout, release, heartbeat and turnover.
- `takeover=true` requires `staff.admin` (operator/admin) and a non-empty `reason`; it writes a `barb.lease.takeover` row to the orchestration audit log and inherits the previous lease's last turnover (its `next_actions`).
- `ttl_sec` is capped at 14400; larger values return 422.
- `POST /api/v1/staff/barb/leases/heartbeat` `{scope, epoch}` extends the caller's own live lease (403 holder mismatch; 409 expired, missing or stale epoch).
- `POST /api/v1/staff/barb/handoff` requires `expected_epoch`.
- `bot`, `fleet-peer` and loopback hold `memory.read` (bot also `memory.shared.summarize`); none holds `memory.write` or the librarian review scope.
- Dispatch and directive writers use this same lease fence (`staff.dispatch_service`, `routers/priorities.py`). `DASHBOARD_BARB_FENCE_MODE` defaults to `enforce` (unknown values fall back to it): `warn` logs and allows an unclaimed Barb-seat write, and `off` skips the fence. A refused unclaimed Barb-seat write is 409 `stale_barb_lease` with reason `claim_required`. Checkout mints the epoch, so it does not send one.
