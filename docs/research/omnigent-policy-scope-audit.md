# Omnigent policy scopes vs Runner Dashboard controls (gap audit)

Issue [#3929](https://github.com/D-sorganization/Runner_Dashboard/issues/3929). Approved research only: compare ideas from [omnigent-ai/omnigent](https://github.com/omnigent-ai/omnigent) (Apache-2.0) against existing Runner Dashboard (RD) quota, budget, and approval code. No code changes are implied by this document; follow-up work is listed for owner review only.

## Method

Omnigent’s public `docs/POLICIES.md` and built-in policy modules under `omnigent/policies/builtins/` (cost, safety, github, orchestration) were read on GitHub. RD behavior was traced in the backend modules cited below. Wording here is paraphrased; nothing is copied verbatim from Omnigent.

## How the two systems think about “policy”

**Omnigent** attaches declarative **function policies** at three scopes, evaluated in order (session → agent spec → server config). Each policy returns **ALLOW**, **DENY**, or **ASK** (pause for human approval). Policies compose in declaration order; any **DENY** stops the chain. Enforcement runs at multiple **phases** (notably LLM **request** and harness **tool_call**, plus attachment admission on upload routes).

**Runner Dashboard** spreads similar concerns across **principal quotas**, **staff role budgets**, **action-proposal approval**, **rate limits**, and **conversation guards**. There is no single composable policy engine or shared ALLOW/DENY/ASK verdict type. Mutations that touch the fleet usually become **ActionProposal** cards on a **thread**, not inline tool-call gates inside a provider CLI session.

### Evaluation order contrast

| Step | Omnigent | Runner Dashboard |
|------|----------|------------------|
| 1 | Session policies (strictest first) | Per-request auth + `staff/rate_limit.py` token bucket on `(principal, action)` |
| 2 | Agent YAML policies | Role permission checks (`check_role_permission`), registry risk class |
| 3 | Server config policies | Principal quotas (`quota_enforcement`), optional capacity reservation |
| 4 | Human gate | **ASK** parks tool/request until UI approval | **ActionProposal** card + `evaluate_approval_authority`; chat may auto-run read/low/medium via `owner_requests.request_approves` |
| 5 | Spend refresh | Session `total_cost_usd` + optional owner daily store | Run store + YAML principal spend + role `spend_by_role_since` |

Omnigent’s **ASK** is synchronous with the harness hook: the tool or LLM turn waits. RD’s approval path is **asynchronous** relative to chat streaming—proposals appear as messages, and execution happens after an explicit decision (or eligible auto-approve rules).

### Omnigent built-ins reviewed (paraphrased)

Beyond the scope table, these Omnigent families were in scope for [#3929](https://github.com/D-sorganization/Runner_Dashboard/issues/3929):

- **Cost** — `cost_budget` (per session) and `user_daily_cost_budget` (per owner, UTC day), including soft **ASK** checkpoints and expensive-model downgrade behavior when hard caps hit.
- **Safety** — `max_tool_calls_per_session`, `detect_loop` (repeated identical tool+args), `ask_on_os_tools` / native write tool sets, `block_skills`, `enforce_sandbox`, `deny_pii_in_llm_request`.
- **Integrations** — `github_policy` (MCP + parsed `git`/`gh` shell), plus Google Drive and orchestration helpers that reuse the same tool_call phase.
- **Server config** — filesystem attachment allow/deny/size/count limits (filename extension admission, not content inspection).

RD has analogues only where staff **actions** or **dispatch** already intercept work; it does not re-run a policy chain on every native CLI tool event.

## Scope mapping (Omnigent → RD)

| Omnigent scope | What it controls | RD nearest equivalent | Coverage |
|----------------|------------------|------------------------|----------|
| **Server-wide** (admin YAML / REST) | Default policies for every session; attachment byte/file caps; optional `user_daily_cost_budget` across all of a user’s sessions (UTC day) | `identity.Quota` on principals in `principals.yml` (`agent_spend_usd_day`, `max_runners`, `local_app_slots`) enforced in `quota_enforcement.py`; fleet `CapacityLimits.max_daily_budget_usd` in `staff/capacity_reservation.py`; env-driven staff rate limits in `staff/rate_limit.py` | **Partial** — USD and concurrency caps exist, but not as composable server policies, not session-tree aware, and not tied to LLM **request** phase |
| **Agent spec** (agent YAML `policies:`) | Per-agent tool limits, repo allowlists (e.g. GitHub read/write), sandbox transforms, cost/tool gates packaged with the agent definition | `staff/role_spec.py` (`RoleSpec`: `budget_usd_per_day`, `budget_usd_per_run`, `budget_max_window_percent`, `permissions.approvals`, `permissions.code_read_only`, provider lists) plus `staff/budget.py` and `staff/quota.py` (subscription window headroom) | **Partial** — role files carry budget and permission hints, but no declarative tool-call policy list or MCP/repo allowlist engine at agent start |
| **Session / chat** (UI toggles or `sys_add_policy`) | End-user overrides evaluated **first**; session-scoped spend (`cost_budget`), per-session tool-call counts, OS/shell **ASK**, loop detection on repeated identical tool calls | `staff/conversations.py` threads; `staff/loop_guard.py` (consecutive **role/bot turns** without a human message, not tool-call counts); `staff/owner_requests.py` (chat requester auto-executes low/medium proposals); chat turns use read-only sandbox in `staff/chat.py` | **Partial** — thread pause and proposal flow cover some “ASK” intent, but not harness-native PreToolUse gates or session-local policy CRUD |

## Policy families (detailed comparison)

| Policy family (Omnigent) | Behavior (summary) | RD equivalent | Gap / partial / covered |
|--------------------------|-------------------|---------------|-------------------------|
| **Session LLM spend** (`cost_budget`) | Cumulative session USD at request + tool_call; soft thresholds **ASK** once per checkpoint; hard cap acts as expensive-model **downgrade gate** | `BudgetGuard.can_chat` / role daily USD in `staff/budget.py`; run-level estimates in scheduler; no per-thread LLM ledger | **Gap** — no session-scoped USD accumulator with threshold approvals or model-tier downgrade gate |
| **Owner daily LLM spend** (`user_daily_cost_budget`) | Same gating logic aggregated per session **owner** across all sessions (UTC day), with cross-session remembered soft approvals | `quota_enforcement.check_dispatch_quota` + `Principal.quotas.agent_spend_usd_day`; staff `GET /api/staff/usage` rollups | **Partial** — principal daily dispatch spend is tracked, but not wired to inline tool/request ASK and not merged with staff chat LLM cost the same way |
| **Tool-call volume cap** (`max_tool_calls_per_session`) | DENY after N tool calls in one session | No staff-chat counter; `LoopGuard` limits agent **messages**, not tools; vuln-scan executor has its own `max_turns` | **Gap** |
| **Repeated tool loop** (`detect_loop`) | ASK when identical tool+args repeats within a window | `LoopGuard` (human-absence turn cap) only | **Gap** |
| **OS / shell / native write ASK** (`ask_on_os_tools`, orchestration write sets) | **ASK** before native or `sys_os_*` mutations | Staff chat read-only mode; risky work routed to **ActionProposal** with risk classes in `staff/actions.py` + `staff/approval_authority.py` | **Partial** — fleet mutations are gated, but provider-native tools inside a chat turn are not policy-evaluated per call |
| **Integration allowlists** (e.g. `github_policy`) | Repo/branch-scoped read/write with **ASK** when shell target is ambiguous | Repo scope on `RoleSpec.repos`; dispatch/work-request validation; no MCP tool-name policy layer | **Gap** for fine-grained, session-local GitHub tool gating |
| **Attachment admission** (server config keys) | Per-session file count/bytes/extension limits on upload routes | Not applicable to RD Staff Console chat attachments the same way | **Out of scope / gap** if RD ever adds comparable attachment delivery |
| **Policy runtime API** | Add/remove policies on a live session; server admin REST | Role/principal edits via admin routes; no session policy registry | **Gap** |
| **Risk-based action approval** (implicit via **ASK** + cards) | User approves parked tool/request | `ActionProposalRecord` lifecycle, `evaluate_approval_authority`, `SILENT_APPROVE_RISKS`, decision SLA in `staff/decision_sla.py` | **Covered** for **registered staff actions**, weaker for raw CLI tool streams |
| **Subscription / plan windows** (not Omnigent-specific, but RD strength) | Omnigent cost policies assume priced `total_cost_usd` | `staff/quota.py` reads Claude/Codex rolling windows; `BudgetGuard` couples USD + window ceiling | **Covered** on RD side (orthogonal to Omnigent’s session USD model) |
| **Sandbox / environment** (`enforce_sandbox`) | Transform agent launch sandbox (network, paths, env passthrough) | Chat read-only sandbox flags; role `permissions.code_read_only`; no launch-time policy transform | **Partial** |
| **PII in prompts** (`deny_pii_in_llm_request`) | Scan user/LLM text; DENY or ASK | `staff/redaction.py` on **stored** messages; publish guard for repo leaks—not live prompt scanning | **Gap** for live LLM request phase |
| **Skills blocklist** (`block_skills`) | DENY named skills | No skill-loader surface in RD staff chat | **Out of scope** today |
| **Admin policy REST** | Runtime add/remove server policies | Admin principal/token routes; no policy registry API | **Gap** |

### Approval semantics side-by-side

| Concern | Omnigent | Runner Dashboard |
|---------|----------|------------------|
| Who can approve | Session participant via UI round-trip on **ASK** | Principals with `staff.approve` scope; owner/admin roles in `approval_authority.is_owner` |
| What is being approved | Specific tool call or LLM request phase | Hash-bound action params (`params_hash`) on proposals |
| Silent/auto approve | User can accept **ASK** once per cost checkpoint | `SILENT_APPROVE_RISKS` + decision SLA for read/low; chat requester path for medium (and Barb high) |
| Expiry | Policy-specific session state | `PROPOSAL_TTL_SECONDS` (24h) on proposals |

## Ranked follow-up slices (value vs effort)

Each slice is intended to stay under 500 changed lines, target `main` directly, and **not** be stacked. No GitHub issues are filed here per [#3929](https://github.com/D-sorganization/Runner_Dashboard/issues/3929).

1. **Design note + metrics hook for session/chat USD** — Document how staff chat could expose a thread-level spend field (even read-only) aligned with existing run store pricing in `staff/pricing.py`. *High value, low effort* (docs + one API field), unblocks later gates.
2. **Principal daily spend visibility in Staff Console** — Surface `quota_enforcement` / usage rollups next to role budget panes so operators see the same numbers dispatch already enforces. *High value, medium effort*.
3. **Tool-call budget for chat turns** — Counter persisted on thread metadata (similar spirit to Omnigent session state), DENY with SC-F3 envelope when exceeded; default conservative limit via env. *High value, medium effort*.
4. **Repeated-tool loop detector for streaming chat** — Hash recent tool names+args from provider stream events; ASK via system message + owner ping before burn continues. *Medium value, medium effort*.
5. **Role-scoped repo write guard for staff dispatch** — Extend work-request validation using `RoleSpec.repos` for write actions (narrower than Omnigent’s full github_policy). *Medium value, lower effort than a full policy engine*.
6. **Unified policy engine** — Replicate Omnigent’s three-tier composable evaluator inside RD. *High value, very high effort* — defer unless product direction explicitly wants agent-host parity.

## Conclusion

RD already **covers** fleet-oriented **action approval** (risk classes, owner-only paths, proposal cards) and **multi-layer spend control** (principal quotas, per-role daily USD, subscription window headroom, fleet capacity reservation). It does **not** cover Omnigent’s **session-first** model: composable policies, harness **tool_call** / LLM **request** phase gates, session-local **ASK** on native tools, session/tool-call counters, or cross-session **owner daily** soft thresholds with remembered approvals.

Gaps are real but narrow for RD’s operator-console mission: the largest mismatches are **inline chat/tool enforcement** and **declarative policy composition**, not the absence of any spend or approval mechanism.

Nothing in this audit recommends porting Omnigent’s agent-host architecture wholesale; the Expert Panel direction for [#3929](https://github.com/D-sorganization/Runner_Dashboard/issues/3929) was **borrow ideas only**.

## RD reference index

| Area | Primary modules |
|------|-----------------|
| Principal quotas & dispatch spend | `backend/quota_enforcement.py`, `backend/identity.py`, `backend/runner_lease.py` |
| Role budgets & alerts | `backend/staff/budget.py`, `backend/staff/role_spec.py` |
| Provider subscription windows | `backend/staff/quota.py` |
| Action approval | `backend/staff/approval_authority.py`, `backend/staff/actions.py`, `backend/staff/conversation_proposals.py` |
| Chat/session guards | `backend/staff/rate_limit.py`, `backend/staff/loop_guard.py`, `backend/staff/owner_requests.py` |
| Fleet capacity USD pool | `backend/staff/capacity_reservation.py` |
