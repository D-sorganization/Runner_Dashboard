# Fleet Tooling Capabilities Matrix, Bean-Counter Ledger, Agent Report Cards & Multi-Barb Protocol

Synthesized with the 3-Seat Expert Panel (`th_5e95506efbf0`: Astra/`codex`, Argon/`antigravity`, Fable/`claude`).

## 1. Specialist Role: `tooling-scout` — CLI vs. Desktop App Capability Matrix

| Agent / Tool | Headless CLI Adapter | Desktop / GUI Surface | Best-Fit Workload Tier | Key Strengths & Constraints |
|---|---|---|---|---|
| **Claude Code / Desktop** (`claude`) | `claude -p --output-format stream-json --verbose` (`CLAUDE_CONFIG_DIR=~/.claude-rd`) | Claude Desktop + MCP + Computer Use (`~/.claude/skills/barb-operator/SKILL.md`) | **Complex & Standard** (`chief-architect`, `issue-remediator`, `pr-remediator`, `code-reviewer`) | Highest first-pass yield on multi-file refactors and PR remediation. Keep `~/.claude` and `~/.claude-rd` credentials synced across all 4 nodes. |
| **Codex CLI / Desktop** (`codex`) | `codex exec --json --full-auto` | Codex Desktop (`~/.codex/skills/barb-operator/SKILL.md`) | **Complex & Standard** (`pragmatic-programmer`, `test-hygiene`, `issue-remediator`) | Strong deterministic verification and low input-to-output token overhead. |
| **Antigravity / Gemini CLI** (`antigravity` / `agy`) | `gemini -p --output-format stream-json --yolo` | Gemini / Antigravity Desktop (`~/.gemini/skills/barb-operator/SKILL.md`) | **Routine / Read-Only Triage** (`night-watch`, `tooling-scout`, `bean-counter`, `sanitation`) | Fast context scanning; avoid routing complex multi-file `issue-remediator` tasks to `gemini-3.8-flash-high` where empirical first-pass yield is low. |
| **Cursor Agent / Desktop** (`cursor-barb`) | Cursor CLI / Local Exec | Cursor Desktop + Browser / Canvas (`~/.cursor/skills/barb-operator/SKILL.md`) | **Interactive GUI / Browser / OAuth / Visual Inspection** | Ideal for `needs_desktop` handoffs requiring browser SSO/OAuth login, visual layout verification, or interactive Canvas authoring. |
| **Grok Bot** (`grok-barb`) | Executor + Fleet SSH/WSL Orchestration | Box Browser + Multi-Machine Local Exec | **Fleet-Wide Barb Coordinator & Cross-Node Steward** | Coordinates all 4 nodes (`DeskComputer`, `ControlTower`, `OGLaptop`, `Brick`), dispatches panels, and audits turnover compliance. |

## 2. CLI <-> Desktop App Collaborative Handoff Protocol

Tasks are classified zero-LLM-cost via `POST /api/v1/staff/barb/classify-surface` (or automatically on `POST /api/v1/staff/barb/inventory`):
1. **`request_desktop` (`status = needs_desktop`):** Headless CLI agent hits a step requiring interactive GUI/browser/OAuth/visual inspection and calls `POST /api/v1/staff/barb/handoff` with `action="request_desktop"`, incrementing `fencing_epoch`.
2. **`claim_desktop` (`status = desktop_claimed`):** Desktop agent (`cursor-barb`, `claude-barb`, `codex-barb`, `agy-barb`, `grok-barb`) claims the task with `expected_epoch`, preventing split-brain execution.
3. **`complete_desktop` (`status = resume_ready`):** Desktop agent completes the interactive step, attaches `artifact_uri` and `resume_notes`, and transitions the task back to `headless_cli`.
4. **`resume_cli` (`status = in_progress`):** Headless CLI agent resumes with the desktop artifact and completes the workflow.

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
