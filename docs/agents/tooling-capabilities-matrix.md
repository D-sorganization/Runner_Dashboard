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

## 5. Current Fleet Empirical Capability State (Audit 2026-10-23)

**Data Source:** `/api/staff/report-cards` (14-day rolling window; 170 terminal runs analyzed)  
**Audit Method:** Zero-LLM-cost analysis; Wilson 95% confidence intervals; empirical first-pass success rates

### CLI Agent Capability Leaderboard

| Rank | Provider | Model | First-Pass Success | Grade | Sample | Key Metrics |
|---|---|---|---|---|---|---|
| **1** | claude | default | 57% [47–67%] | **B** | 93 runs | High rework (43%); suitable for complex roles (`chief-architect`, `pr-remediator`, `code-reviewer`) |
| **2** | claude | sonnet | 25% [5–70%] | **D** | 4 runs | Low sample; auth issues; avoid until stabilized |
| **3** | codex | gpt-6-luna | 30% [17–48%] | **D** | 30 runs | 79% token waste; skill loading errors (design-by-contract YAML); 1 cli_missing failure on DeskComputer |
| **4** | antigravity | gemini-3.8-flash-high | 16.7% [5–45%] | **F** | 12 runs | 93% token waste; unsuitable for complex tasks; read-only triage only (night-watch, sanitation, bean-counter) |
| **5** | codex | default | 13.8% [6–31%] | **D/F** | 29 runs | 86% rework; similar failures as gpt-6-luna |

**Routing Recommendation:**
- **Primary:** `claude` (default) for all standard & complex workloads (57% first-pass yield).
- **Secondary:** `antigravity` for advisory/read-only triage only (16% success; high token waste).
- **Avoid Until Fixed:** `codex` (binary missing on DeskComputer; skill loading errors; 79–89% token waste).
- **Avoid:** `claude` (sonnet) until leaderboard sample size >20 runs and auth issues resolved.

### Fleet Node Status & Auth Alerts (2026-10-23T05:35Z)

| Node | Status | claude Auth | codex Auth | antigravity Auth | pr-remediator Status |
|---|---|---|---|---|---|
| **DeskComputer** | ✅ Online | ⚠️ Expiring (~2.5h) | ✅ OK | ⚠️ Stale (131d) | running |
| **ControlTower** | ✅ Online | ⚠️ Expiring (~1.8h) | ✅ OK | ⚠️ Stale (131d) | running |
| **Brick** | ✅ Online | ❌ **Expired** (2026-10-15) | ✅ OK | ✅ OK | ❌ **DEAD** (liveness stale 7d) |
| **OGLaptop** | ❌ **OFFLINE** | — | — | — | — |

**Critical Actions (Next 2 Hours):**
- Refresh claude OAuth on DeskComputer, ControlTower, Brick (see `docs/runbooks/durable-provider-credentials.md`).
- Refresh antigravity subscription token on all nodes (stale >90 days).
- Restore codex binary on DeskComputer or route tasks to ControlTower/Brick.
- Fix codex skills: verify `~/.codex/skills/design-by-contract/SKILL.md` YAML frontmatter (must start/end with `---`).

### barb-operator SKILL.md Installation Status

✅ **Claimed Deployed:** Recent turnover (grok-barb, 2026-10-23T05:19:38Z) states "Installed universal barb-operator SKILL.md across codex, claude, gemini, and cursor on all 4 nodes" (PR RD #2285, PR RM #2364).

⏳ **Verification Deferred:** Headless CLI cannot verify GUI app skill installations; requires interactive desktop session. Expected paths:
- `~/.claude/skills/barb-operator/SKILL.md` (Claude Desktop)
- `~/.codex/skills/barb-operator/SKILL.md` (Codex Desktop)
- `~/.gemini/skills/barb-operator/SKILL.md` (Gemini/Antigravity Desktop)
- `~/.cursor/skills/barb-operator/SKILL.md` (Cursor Desktop)

**Source of Truth:** `Repository_Management/skills/fleet/barb-operator/SKILL.md` (universal skill for all desktop personas).

### Handoff Protocol & Multi-Barb Continuity

✅ **Live & Active:** 6 scoped Barb roles, 6 app personas, active lease enforcement, turnover compliance checks.

⚠️ **Compliance Alert:** `rd-barb` currently holds `barb:dispatch-coordinator` lease (expires 2026-10-23T07:19:38Z) **without logged turnover**. Must release lease with `turnover_summary` or pending turnover entry before expiration.

**See Also:** `AGENTS.md` → Fleet Core Rules → PR Lifecycle; lane-prs.md for concurrent Barb role checkout patterns.
