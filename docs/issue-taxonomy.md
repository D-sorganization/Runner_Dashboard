# Issue Taxonomy and Agent Dispatch Contract

This document is the **canonical reference** for how issues in
`runner-dashboard` are categorized so that:

1. Human maintainers can see priority and progress at a glance.
2. Autonomous agents can pick up work they are qualified for and skip work they
   are not.
3. The dashboard (and any future orchestrator) can query structured metadata
   instead of parsing free-text bodies.

The taxonomy is **label-based**. Labels are the source of truth. A GitHub
Projects v2 board layers views on top of the same labels but does not replace
them — labels stay machine-readable even without the board.

Every field in this document is **additive** to the existing severity/domain
labels (`critical`, `high`, `medium`, `low`, `backend`, `frontend`, ...). Those
stay as they are.

---

## Why this exists

We run many autonomous agents against this repo (see `CLAUDE.md` for the
priority order). Without structured metadata:

- Agents redundantly pick the same issue or pick issues beyond their skill.
- Quick wins are buried under larger tickets because both look the same on the
  issue list.
- Opinion-driven tickets get silently resolved the wrong way by whichever
  agent reaches them first.
- There is no way to ask "get me three fast, objective, backend tickets"
  without reading every issue.

The taxonomy fixes all four.

---

## Label dimensions

Every non-epic issue **should** carry at minimum one label from each of the
**required** dimensions below. Epics carry the `type:epic` label and may omit
`effort` and `complexity` (those belong on the children).

### 1. Type (required, single-valued)

| Label | Meaning |
|---|---|
| `type:epic` | Parent tracking issue. No direct work happens here. Children are linked as sub-issues. |
| `type:task` | Ordinary unit of work. Default for most issues. |
| `type:bug` | Defect in existing behavior. |
| `type:security` | Security finding. Often overlaps with `type:bug`; prefer `type:security` when the label `security` is present. |
| `type:research` | Investigation / spike. Answer unknown; must investigate before implementation. |
| `type:docs` | Documentation-only change. |
| `type:chore` | Housekeeping: deps, CI, lint, formatting. |

### 2. Complexity (required, single-valued) — **skill required of the agent**

This dimension is deliberately **not about difficulty of the ticket** — it is
about the **skill and breadth of context** an agent must bring. A small ticket
can be deep if it touches a subtle invariant.

Agent names do not appear here — orchestrators map complexity → agent pool.

| Label | Meaning | Typical work |
|---|---|---|
| `complexity:trivial` | Single-file mechanical change. No judgement, no architectural awareness. | Rename a symbol, fix a typo, delete dead code, update a constant, bump a pinned version. |
| `complexity:routine` | Well-scoped change, clear spec, may touch multiple files within one subsystem. | Add a validated field to a request payload, add a test for a documented behavior, add a route that mirrors an existing one. |
| `complexity:complex` | Cross-cutting change, architectural awareness needed. May require reading 10+ files and understanding conventions. | Add a new FastAPI dependency that threads request context; introduce a new lease type in the autoscaler. |
| `complexity:deep` | Requires deep codebase knowledge and/or novel design. | Reshape the dispatch contract; redesign runner ownership; rework the frontend state model. |
| `complexity:research` | The right answer is unknown. Must investigate and write up options **before** anyone implements. | "How should we authenticate bots?"; "Is Playwright viable for the SPA?". |

### 3. Effort (required, single-valued) — **size of the work**

Independent of complexity. A `complexity:trivial` ticket can be `effort:l` if
it touches 200 files. A `complexity:deep` ticket can be `effort:s` if the
change itself is small once you know what to change.

| Label | Rough bound | Notes |
|---|---|---|
| `effort:xs` | < 1 hour | Single PR, single file usually. Candidate for `quick-win`. |
| `effort:s` | < 4 hours | Bounded, clear scope. |
| `effort:m` | < 1 day | Normal feature or bugfix. |
| `effort:l` | 1–3 days | Decompose if possible, but acceptable as-is. |
| `effort:xl` | > 3 days | **Should be an epic with children**, not a single ticket. |

### 4. Quick win (optional, boolean)

| Label | Meaning |
|---|---|
| `quick-win` | High perceived value, low effort. Surface these to new contributors and fast agents. Typically `effort:xs`/`effort:s` and `complexity:trivial`/`complexity:routine`. |

### 5. Judgement (required, single-valued) — **how much opinion is involved**

This is the dimension the orchestrator uses to decide whether to dispatch a
single agent or request a panel review first.

| Label | Meaning | Dispatch rule |
|---|---|---|
| `judgement:objective` | Most competent agents would agree on the correct implementation. A spec or failing test defines success. | Dispatch directly. |
| `judgement:preference` | Multiple valid approaches. Style, ergonomics, or naming matters, but the differences are small. | Dispatch directly; human review on PR is enough. |
| `judgement:design` | Architectural decision with durable consequences. Needs deliberate choice. | **Do not dispatch for implementation until a design has been chosen.** Request a panel review or a human decision. |
| `judgement:contested` | Active disagreement is already on the issue. | **Hold.** Panel review required. |

### 6. Panel review (optional, boolean)

| Label | Meaning |
|---|---|
| `panel-review` | Periodic agent-panel review is requested or in progress. The panel workflow (see `.github/workflows/agent-panel-review.yml`) will post opinions as comments on these issues on its schedule. |

Typically paired with `judgement:design` or `judgement:contested`. Can also be
applied to any issue where the author wants multi-agent input before work
starts.

### 7. Skill domain (optional, multi-valued)

These already mostly exist. Keep them. They tell an orchestrator which agent
pool has the relevant expertise.

`backend`, `frontend`, `ci`, `github-actions`, `deploy`, `infra`, `docs`,
`tests`, `security`, `code-quality`, `supply-chain`, `dependencies`,
`observability`, `reliability`, `configuration`, `secrets`,
`agent-safety`, `cost-control`, `a11y`, `governance`

An issue may carry several of these. New domains are fine — keep names
lowercase and hyphenated.

### 8. Priority (required, single-valued)

Existing convention, kept as-is:

`critical`, `high`, `medium`, `low`

A security severity and a work priority are not the same thing, but in this
repo today they are aligned. If they diverge in future, split into
`severity:*` and `priority:*`.

### 9. Wave (optional, single-valued)

Execution-order bucket, used by the meta-tracking issue #55. Values:
`wave:1`, `wave:2`, `wave:3`, `wave:4`, `wave:5`.

### 10. Rollout (label-triggered automation)

These labels drive `Taxonomy Rollout` (`.github/workflows/taxonomy-rollout.yml`).
Apply to an epic (not ordinary issues) to kick off a migration.

| Label | Meaning |
|---|---|
| `rollout:taxonomy` | User-applied. Triggers the taxonomy rollout's **preview** stage (creates labels, runs backfill dry-run, posts preview comment). |
| `rollout:taxonomy-apply` | User-applied **after reviewing the preview**. Triggers the **apply** stage (real label + sub-issue changes). |
| `rollout:in-progress` | Workflow-managed. Indicates a rollout has started. Do not apply by hand. |
| `rollout:done` | Workflow-managed. Indicates a rollout completed successfully. |

The rollout chain is idempotent and reversible — labels are only added,
sub-issue edges can be unlinked, and the manifest is editable.

### 11. Lease (automatic, single-valued)

Managed by the lease-reaper workflow. Do not set by hand.

`claim:user`, `claim:maxwell-daemon`, `claim:claude`, `claim:codex`,
`claim:jules`, `claim:local`, `claim:gaai`.

---

## Issue body contract

In addition to labels, every non-epic issue body **should** start with a
machine-parseable metadata block. Labels win if they disagree, but the block
makes the intent legible in a single glance and lets agents without label
access still triage.

```markdown
<!-- dispatch-metadata:v1
type: task
complexity: routine
effort: s
judgement: objective
quick_win: true
panel_review: false
domains: [backend, security]
wave: 2
depends_on: [#16]
blocks: []
-->
```

Fields:

- `type`, `complexity`, `effort`, `judgement` — required, one value each.
- `quick_win`, `panel_review` — optional booleans, default `false`.
- `domains` — list; must match existing domain labels.
- `wave` — integer 1–5, or omitted.
- `depends_on`, `blocks` — issue references. Use sub-issue links for real
  parent/child; use these for lateral dependencies.

The issue-template forms in `.github/ISSUE_TEMPLATE/` produce this block
automatically.

---

## Agent dispatch contract

Orchestrators (the dashboard, Jules Control Tower, future dispatchers) choose
work using the following rules. Agents should self-enforce these, not rely on
the orchestrator alone.

### Pickable work

An issue is **pickable** by an agent if **all** of the following hold:

1. State is `open`.
2. No open PR currently references it (check `closes #N` / `fixes #N`).
3. No active `claim:*` lease.
4. The agent's skill tier covers the issue's `complexity`. The mapping is
   orchestrator-defined, but a reasonable default is:
   - tier-1 agents: `complexity:trivial`, `complexity:routine`
   - tier-2 agents: above + `complexity:complex`
   - tier-3 agents: above + `complexity:deep`
   - research-capable agents: `complexity:research`
5. The agent's domain capabilities cover the issue's `domains` labels.
6. `judgement` is **not** `judgement:design` or `judgement:contested` — unless
   the agent is explicitly a panelist.
7. The issue has a `dispatch-metadata` block or full label set. Missing
   metadata means "unreviewed"; agents must not guess.

### Quick-win lane

Agents that want to do a low-risk warmup should filter for
`quick-win AND complexity:(trivial|routine) AND effort:(xs|s) AND
judgement:objective`. That is the safe pool.

### Panel lane

Issues with `panel-review` are explicitly seeking agent opinions, not code.
The panel workflow enforces: comment with an opinion, do **not** open a PR.
See the workflow file for the full contract.

---

## Panel review

Some issues are opinion-driven (`judgement:design`, `judgement:contested`, or
manually flagged `panel-review`). For those we want multiple agents to weigh
in **before** anyone implements.

### Two Phases: Manual → Automated

**Phase 1 (Current):** Manual design consensus via agent opinions

Until `.github/workflows/agent-panel-review.yml` is fully configured with agent
panels, design consensus happens manually:

1. Any agent can post a design opinion in the structured format (see below)
2. At least 2 qualified agents should post opinions on design issues
3. Opinions converge on an approach
4. A human maintainer reviews opinions and relabels issue:
   - `judgement:design` → `judgement:objective` (approved, ready to implement)
   - `judgement:design` → `judgement:contested` (disagreement; needs escalation)
5. Once relabeled to `judgement:objective`, agents can implement

**Phase 2 (Automated):** Formal panel workflow

Once the `Agent Panel Review` workflow is fully configured:

1. Issues labeled `panel-review` trigger automatic panel dispatch on schedule
   (weekly) or on-demand
2. For every panel-review issue, the workflow:
   - Collects issue body, dispatch-metadata block, recent comments
   - Dispatches the configured agent panels
   - Each panelist posts exactly one comment in structured format (below)
   - Workflow posts summary with tally and dominant stance
3. Maintainer reviews and relabels to unblock implementation

### Opinion Format (Both Phases)

```
<!-- panel-opinion:v1 agent=<agent-id> stance=support|oppose|modify -->
## Opinion
<2-3 sentence summary>

## Suggested approach
<specific recommendation>

## Risks
<identified risks or concerns>
```

**Rules:**
- Post exactly one opinion per agent per issue
- Opinions are advisory; maintainer makes final call
- `stance` is your position: `support` (go ahead), `oppose` (don't), `modify` (approve with changes)
- Agents **never** open PRs from design/panel-review issues — only post opinions

### Relabeling to Unblock Implementation

Maintainer's responsibility once opinions converge:
- Change `judgement:design` → `judgement:objective` to unblock implementation
- Or escalate to `judgement:contested` if there's genuine disagreement
- Or close as `not planned` if consensus is "don't do this"

---

## Examples

Two real issues, showing how they should be labeled:

**#16 — "No authentication on any /api/* endpoint"**
- `type:security`, `security`, `backend`, `critical`
- `complexity:complex` (threading auth through 67 routes)
- `effort:l`
- `judgement:design` (OAuth-vs-token-vs-session is a real choice)
- `panel-review` (once, to pick direction)
- `wave:1`

**#54 — "Assorted low-severity findings: hardcoded paths, log rotation, ..."**
- `type:chore`, `code-quality`, `low`
- `complexity:trivial`
- `effort:s`
- `judgement:objective`
- `quick-win`

---

## Changes to existing process

- **`[CRITICAL]/[HIGH]/[MEDIUM]/[LOW]` title prefixes** stay for now, but are
  advisory. The priority label is authoritative.
- **`[TRACKING]` and `[EPIC]` title prefixes** stay. Add `type:epic`.
- **Sub-issue links** replace markdown checklists in epic bodies. Existing
  checklists in #55 and #63 will be migrated by the migration epic.
- **CI enforcement** is deliberately out of scope for the first migration.
  After the backfill lands cleanly, a `ci-issue-taxonomy.yml` check can
  enforce the required labels on new issues. See the migration plan for
  details.
