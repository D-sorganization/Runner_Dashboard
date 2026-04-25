# Panel Review Agent Tiers Configuration

This document defines the agent roster and tier structure used by the Agent Panel Review workflow (`.github/workflows/agent-panel-review.yml`) to automatically request opinions on design-phase issues.

---

## Agent Tiers

### Tier 1: Routine & Quick-Win Reviewers

**Agents:** `claude`, `codex`

**Qualifications:**
- Complexity: `trivial`, `routine`
- Effort: `xs`, `s`
- Domains: `backend`, `frontend`, `tests`, `ci`, `code-quality`

**Use case:** Fast turnaround on well-scoped, straightforward design questions.

Example: "Should we use middleware or decorators for rate limiting?"

### Tier 2: Complex Task Reviewers

**Agents:** `claude`, `maxwell-daemon`, `codex`

**Qualifications:**
- Complexity: up to `complex`
- Effort: up to `m`
- Domains: `backend`, `frontend`, `architecture`, `security`, `agent-safety`

**Use case:** Cross-cutting design decisions that require architectural awareness.

Example: "How should we restructure the dispatch contract to support retries?"

### Tier 3: Deep Work Reviewers

**Agents:** `claude`, `maxwell-daemon`, `jules`

**Qualifications:**
- Complexity: up to `deep`
- Effort: `l`, `xl`
- Domains: `security`, `architecture`, `supply-chain`, `governance`

**Use case:** Major architectural changes with long-term implications.

Example: "Should we switch from JWT-based auth to mutual TLS for inter-node communication?"

### Research Tier: Investigation Reviewers

**Agents:** `claude`

**Qualifications:**
- Complexity: `research`
- Domains: `research`, `spike`, `feasibility`

**Use case:** Exploration and feasibility studies.

Example: "Is Playwright viable as a browser automation tool for the SPA tests?"

---

## How Panel Review Dispatch Works

### Automatic Dispatch (Workflow-Driven)

1. **Trigger:** Issue labeled with `panel-review`
2. **Workflow Runs:** `.github/workflows/agent-panel-review.yml` dispatches
3. **Tier Selection:** Workflow extracts `complexity:*` label, selects appropriate tier(s)
4. **Brief Posted:** Workflow posts a dispatch brief mentioning recommended reviewers
5. **Agent Response:** Agents read the brief and post opinions in structured format
6. **Tally:** Workflow aggregates opinions and posts a summary with stance tally

### Manual Dispatch

Maintainers can manually trigger panel review at any time:

```bash
gh workflow run agent-panel-review.yml \
  -f issue_number=<number> \
  -f mode=brief
```

Or dispatch only the summarize step (if opinions already exist):

```bash
gh workflow run agent-panel-review.yml \
  -f issue_number=<number> \
  -f mode=summarize
```

---

## Opinion Format (Structured)

Panelists must respond with this format:

```markdown
<!-- panel-opinion:v1 agent=<tier> stance=support|oppose|modify -->
## Opinion
<2-3 sentence summary of your position>

## Suggested approach
<specific recommendation or alternative design>

## Risks
<identified risks, concerns, or gotchas>
```

**Fields:**
- `agent`: Your agent tier (e.g., `tier-1`, `tier-2`, `tier-3`, `research`)
- `stance`: Your position — `support` (go ahead), `oppose` (don't), `modify` (approve with changes)

---

## Summary Output Format

The workflow generates a summary comment after at least 2 opinions are received:

```
## Panel summary (opinions=3)

| Stance | Count |
|---|---|
| support | 2 |
| modify | 1 |

**Consensus:** 67% support

### Suggested Approaches
1. Approach A (suggested by @agent1, @agent2)
2. Approach B (suggested by @agent3)

### Identified Risks
- **@agent1**: Risk of breaking existing integrations
- **@agent2**: Token rotation complexity

### Panelists
- @agent1 — `tier-2`, stance `support`
- @agent2 — `tier-2`, stance `modify`
- @agent3 — `tier-1`, stance `oppose`

Once the team has picked a direction, remove the `panel-review` label...
```

---

## Tier Selection Rules

When an issue is labeled `panel-review`, the workflow automatically selects tiers based on complexity:

| Complexity | Recommended Tier(s) |
|---|---|
| `trivial` | Tier 1 |
| `routine` | Tier 1 |
| `complex` | Tier 2 (includes Tier 1) |
| `deep` | Tier 3 (includes Tier 1 & 2) |
| `research` | Research tier |

**Example:** An issue labeled `complexity:complex` will get recommendations for agents from both Tier 1 and Tier 2, with emphasis on Tier 2 experts.

---

## Configuration (Agent Roster)

The agent roster is configured in `.github/scripts/panel-review.js`:

```javascript
const AGENT_ROSTER = {
  "tier-1": {
    agents: ["claude", "codex"],
    complexity: ["trivial", "routine"],
    domains: ["backend", "frontend", "tests", "ci", "code-quality"],
  },
  "tier-2": { ... },
  "tier-3": { ... },
  research: { ... },
};
```

To add or modify agents, edit this configuration and update this document.

---

## Best Practices

1. **Label accurately:** Apply `complexity:*` label correctly so the right tier gets selected
2. **Respond with detail:** Include suggested approach and risks, not just stance
3. **Respect the contract:** Post exactly one opinion per agent per issue
4. **Don't implement yet:** Opinions only; hold implementation until `judgement:objective` label is set
5. **Consensus threshold:** 2+ opinions are needed before summarizing; 2/3 majority is "strong consensus"

---

## Related Issues

- **#113:** EPIC — Automate Agent Panel Review Workflow Integration
- **#114:** Configure panelist roster for Agent Panel Review workflow (this doc)
- **#115:** Integrate panel review with local runner CI pipeline
- **#116:** Implement panel opinion summary and tally logic

---

## Transition from Phase 1 to Phase 2

**Phase 1 (Current):** Agents discover `panel-review` issues and post opinions manually.

**Phase 2 (This Implementation):** Workflow posts dispatch brief with tier recommendations. Agents still post opinions manually, but are now explicitly invited.

**Phase 3 (Future):** Workflow could invoke agents directly via workflow_dispatch or Jules Control Tower integration for even faster feedback.
