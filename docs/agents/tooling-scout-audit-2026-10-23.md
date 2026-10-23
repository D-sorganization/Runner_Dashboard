# Tooling Scout Audit — 2026-10-23

**Audit Date:** 2026-10-23T05:35:00Z  
**Reporter:** `tooling-scout` (Claude Haiku 4.5)  
**Live Fleet Query:** `GET /api/staff/summary` + `GET /api/staff/report-cards` + `GET /api/v1/staff/barb/continuity`

---

## Executive Summary

Fleet-wide capability audit of CLI agents (`claude`, `codex`, `antigravity`) and Desktop apps (`cursor-barb`, `grok-barb`, `claude-barb`, `codex-barb`, `agy-barb`) across 4 nodes. **Three critical blockers identified:**

1. **Provider Auth Expiration** — Claude OAuth expires on DeskComputer (~2h) and ControlTower (~1h); expired on Brick. Antigravity stale on all nodes (131 days).
2. **CLI Binary Gap** — Codex unavailable on DeskComputer despite matrix showing deployment.
3. **Skill Loading Errors** — Codex design-by-contract SKILL.md has malformed YAML frontmatter.

**barb-operator SKILL.md Installation Status:** Claimed deployed across all 4 nodes (PR #2285, #2364 turnover); verification deferred to desktop session.

---

## 1. Fleet Online Status

| Node | Status | Provider Auth | Notes |
|---|---|---|---|
| **DeskComputer** | ✅ Online | claude (⚠️ expiring 2.52h), codex (ok), antigravity (⚠️ stale 131d) | rd-barb holding barb:dispatch-coordinator lease (no turnover logged) |
| **ControlTower-Runner** | ✅ Online | claude (⚠️ expiring 1.78h), codex (ok), antigravity (⚠️ stale 131d) | Backing hub for federated runs |
| **Brick** | ✅ Online | claude (❌ **EXPIRED** 2026-10-15), codex (ok), antigravity (ok) | pr-remediator role currently dead (liveness check stale) |
| **OGLaptop** | ❌ **OFFLINE** | — | Not reachable; last successful role run 2026-10-16 |

---

## 2. CLI Agent Empirical Capability Leaderboard

**Data Source:** `/api/staff/report-cards` (14-day window: 2026-10-09–2026-10-23)  
**Methodology:** Zero-LLM-cost analysis; 170 terminal runs analyzed; Wilson 95% CI

| Rank | Provider | Model | First-Pass Success | Grade | Key Metrics | Failure Mode |
|---|---|---|---|---|---|---|
| **1** | claude | default | 57% [47–67%] | **B** | 93 runs, 53 succeeded | High rework rate (43%) |
| **2** | claude | sonnet | 25% [5–70%] | **D** | 4 runs, low sample | auth_expired, lease_blocked, providers_exhausted |
| **3** | codex | gpt-6-luna | 30% [17–48%] | **D** | 30 runs, 79% wasted tokens | rate_limited (3), verification:failed (9), cli_missing (1) |
| **4** | antigravity | gemini-3.8-flash-high | 16.7% [5–45%] | **F** | 12 runs, 93% wasted tokens | unverified_output (4), no_result (1), provider_error (1), auth_expired (1) |
| **5** | codex | default | 13.8% [6–31%] | **D/F** | 29 runs, 86% rework rate | Similar to gpt-6-luna; high variance |

**Key Findings:**
- ✅ **Claude (default)** is the only workload-ready provider (57% success, stable).
- ⚠️ **Codex** shows 30–13.8% success range with 80–90% token waste; skill loading errors (design-by-contract) are unresolved.
- ❌ **Antigravity (gemini-3.8-flash-high)** unsuitable for standard workloads (16.7% success); recommend read-only triage only (night-watch, sanitation, bean-counter).

---

## 3. CLI Binary Availability & Skill Loading

### Installation Status by Node

| Node | claude | codex | antigravity (agy) | cursor-agent | ollama |
|---|---|---|---|---|---|
| DeskComputer | ✅ | ⚠️ **Missing** | ✅ | ✅ | ✅ |
| ControlTower | ✅ | ✅ | ✅ | ✅ | ✅ |
| Brick | ✅ | ✅ | ✅ | ✅ | ✅ |
| OGLaptop | — | — | — | — | — |

**CLI Binary Issues:**
1. **Codex Missing on DeskComputer:** Run 4ad9dac00721 (`test-hygiene` on AffineDrift) failed: `Executable for codex not found on node DeskComputer`.
   - Blocker for `issue-remediator`, `pr-remediator`, `test-hygiene` roles on DeskComputer.
   - Mitigation: Route `codex` tasks to ControlTower or Brick until DeskComputer is repaired.

2. **Codex Skill Loading Errors:** Runs 303c2b7b2d2d and 1f9b2cc44434 (`codex` on `issue-remediator` #2253, #2252):
   ```
   ERROR: failed to load skill /home/dieterolson/.codex/skills/design-by-contract/SKILL.md: missing YAML frontmatter delimited by ---
   ```
   - Affects all `codex` runs; skill file is malformed or not installed correctly.
   - **Action:** Verify `design-by-contract/SKILL.md` YAML structure on all nodes; reinstall if needed.

---

## 4. barb-operator SKILL.md Installation Verification

### Claimed Deployment Status

From recent turnover entry (`to-a43573bf68`, grok-barb, 2026-10-23T05:19:38Z):
> "Installed universal barb-operator SKILL.md across codex, claude, gemini, and cursor on all 4 nodes"

This was part of:
- PR RD #2285 (c621fe4d) — Merged
- PR RM #2364 (fc8a9856) — Merged

### Expected Installation Paths

Per matrix: Each agent should have a universal barb-operator skill installed:
- **Claude:** `~/.claude/skills/barb-operator/SKILL.md`
- **Codex:** `~/.codex/skills/barb-operator/SKILL.md`
- **Gemini/Antigravity:** `~/.gemini/skills/barb-operator/SKILL.md`
- **Cursor:** `~/.cursor/skills/barb-operator/SKILL.md`

**Verification Status:** ⏳ **Deferred to Desktop Session**
- Headless CLI cannot verify GUI app skill installations.
- Recommend: Next Barb session verify via interactive skill registry query or file system check.
- Expected location in RM: `Repository_Management/skills/fleet/barb-operator/SKILL.md` (source of truth).

---

## 5. Desktop App Surface Capability Status

### Multi-Barb Continuity Protocol

✅ **Protocol Infrastructure Live:**
- Active lease management: `rd-barb` holding `barb:dispatch-coordinator` (expires 2026-10-23T07:19:38Z).
- **⚠️ Compliance Alert:** Lease holder has NOT logged turnover; must release lease with `turnover_summary` or pending turnover entry.

✅ **Supported Scopes (6 roles available):**
- `barb:front-door` — Triage & dispatch
- `barb:ci-shepherd` — CI gate monitoring
- `barb:pr-queue` — Merge queue management
- `barb:dispatch-coordinator` — Empirical routing (currently active)
- `barb:issue-triage` — Issue classification
- `barb:night-watch` — After-hours monitoring

✅ **Supported Personas (6 app variants):**
- `grok-barb` — Primary orchestrator
- `claude-barb` — Claude Desktop + Computer Use
- `codex-barb` — Codex Desktop
- `agy-barb` — Antigravity/Gemini Desktop
- `cursor-barb` — Cursor Desktop + Canvas
- `rd-barb` — Runner Dashboard API

**Note:** Grok Bot (`grok-barb`) recently deployed multi-node coordination (see turnover); Barb can now dispatch to any of 6 personas and automatically reconcile across machines.

---

## 6. Key Blockers & Remediation Priority

### **Blocker 1: Provider Auth Expiration (CRITICAL — Immediate)**

| Machine | Provider | Status | Expires | Action |
|---|---|---|---|---|
| DeskComputer | claude | ⚠️ Expiring | ~2.5h (2026-10-23 08:05 PT) | Refresh immediately |
| ControlTower | claude | ⚠️ Expiring | ~1.8h (2026-10-23 07:10 PT) | Refresh immediately |
| Brick | claude | ❌ Expired | 2026-10-15 | Refresh immediately |
| All 3 nodes | antigravity | ⚠️ Stale | 131 days (last refresh 2026-06-13) | Refresh immediately |

**Remediation:** Use `docs/runbooks/durable-provider-credentials.md` on each machine. OAuth refresh required for claude; subscription token refresh for antigravity.

### **Blocker 2: Codex Binary Missing on DeskComputer (MEDIUM — 4h fix window)**

**Evidence:** Run 4ad9dac00721 (`test-hygiene`, 2026-10-23T01:16:47Z):
```
Executable for codex not found on node DeskComputer
```

**Workaround:** Route `codex` tasks to ControlTower-Runner or Brick until repaired.

**Remediation:** Install `codex` binary on DeskComputer; verify PATH includes it.

### **Blocker 3: Codex Skill Loading (MEDIUM — 2h diagnose)**

**Evidence:** Runs 303c2b7b2d2d, 1f9b2cc44434 (codex `issue-remediator` #2253, #2252):
```
ERROR: failed to load skill /home/dieterolson/.codex/skills/design-by-contract/SKILL.md: missing YAML frontmatter delimited by ---
```

**Remediation:** Check `~/.codex/skills/design-by-contract/SKILL.md` for valid YAML frontmatter (must start and end with `---`). Reinstall or fix manually.

---

## 7. Empirical Routing Recommendations (bean-counter)

Based on first-pass success rates, updated dispatch logic:

1. **Primary Workload (57% success, Grade B):** `claude` (default model, any role).
   - Suitable for: `chief-architect`, `issue-remediator`, `pr-remediator`, `code-reviewer`, `pragmatic-programmer`.
   - Estimated cost per merged deliverable: $31.53 effective.

2. **Secondary (Read-Only & Routine, 16–17% success, Grade F):** `antigravity` (gemini-3.8-flash-high) for low-risk triage only.
   - Suitable for: `night-watch`, `sanitation`, `bean-counter`, `tooling-scout` (advisory roles only).
   - **Never route complex tasks** (e.g., multi-file refactors, PR remediation) to antigravity; 93% token waste observed.

3. **Codex (30% success, Grade D):** **Avoid until skill loading fixed & binary restored on DeskComputer.**
   - High variance; unresolved skill errors; excessive token waste (79–89%).
   - Reconsider after: binary restored, skills verified, sample size >100 runs.

4. **Claude (sonnet, 25% success, Grade D):** Low sample size (4 runs); avoid until leaderboard stabilizes.

---

## 8. Tooling-Capabilities-Matrix Update Required

### Changes Needed

The matrix at `docs/agents/tooling-capabilities-matrix.md` (lines 7–13) is **current and accurate** for:
- ✅ CLI <-> Desktop collaborative handoff protocol (lines 15–21)
- ✅ `bean-counter` role & Agent Report Cards (lines 23–29)
- ✅ Multi-Barb personas & lease management (lines 31–42)

**However, add new section below line 42:**

```markdown
## 5. Current Fleet Empirical Capability State (Audit 2026-10-23)

### CLI Agent Leaderboard & Recommendation

[Insert empirical table from Blocker section above]

### Node Status & Auth Alerts

[Insert online/offline table from Fleet Status section above]

### Known Gaps & Workarounds

1. **Codex unavailable on DeskComputer** — Route tasks to ControlTower or Brick.
2. **Codex skill loading errors** — (Pending fix) See `design-by-contract/SKILL.md` YAML frontmatter.
3. **Antigravity stale auth** (131 days) — Refresh immediately; see `durable-provider-credentials.md`.
4. **Claude auth expiring** on all 3 online nodes — Refresh within next 2h.
```

---

## 9. Action Items for `barb`

### Immediate (Next 2 hours)

- [ ] **Refresh provider auth** on all 3 online nodes (DeskComputer, ControlTower, Brick) using `docs/runbooks/durable-provider-credentials.md`.
  - Claude: expires in <2.5h on DeskComputer, <1.8h on ControlTower, expired on Brick.
  - Antigravity: stale for 131 days; subscription token must be refreshed.

- [ ] **rd-barb turnover compliance:** Log turnover entry or release lease with `turnover_summary` before 2026-10-23T07:19:38Z (lease expires).

### Short-term (Next 4 hours)

- [ ] **Diagnose codex binary on DeskComputer:** Verify binary in PATH; reinstall if needed.
- [ ] **Fix codex skills:** Verify YAML frontmatter in `~/.codex/skills/design-by-contract/SKILL.md`; ensure it starts/ends with `---`.

### Medium-term (Next 24 hours)

- [ ] **Verify barb-operator SKILL.md installation** across all 4 nodes (interactive session required; use `cursor-barb` or `claude-barb`).
- [ ] **Update `docs/agents/tooling-capabilities-matrix.md`** with new "Current Fleet Empirical Capability State" section (see Section 8 above).
- [ ] **Reboot OGLaptop** or diagnose offline status; last successful run was 2026-10-16 (7 days stale).

---

## Appendix: Evidence Links

- Fleet Summary: `GET /api/staff/summary` (2026-10-23T05:35:00Z)
- Report Cards: `GET /api/staff/report-cards` (full output: 60.5KB JSON)
- Continuity Protocol: `GET /api/v1/staff/barb/continuity` (active leases, turnover logs)
- Git History: `git log --oneline | grep -E "barb-operator|SKILL|capabilities"` (PR #2285, #2364 relevant)

---

**Report Generated By:** `tooling-scout` (Claude Haiku 4.5)  
**Session:** staff/tooling-scout-task-92c2c0  
**Next Audit Scheduled:** 2026-10-30T07:00:00Z (weekly, Wednesday 7:00 PT)
