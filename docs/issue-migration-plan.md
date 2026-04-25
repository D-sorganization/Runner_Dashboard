# Issue Organization Migration Plan

Migration from free-form issues + markdown-checklist tracking issues to a
labeled, sub-issue-hierarchical, agent-dispatchable taxonomy.

This is the plan; tracking of execution lives in the migration epic on
GitHub.

## Goal

Make every issue legible to both humans and autonomous agents at a glance,
so that:

- Agents can self-select work that matches their skill tier.
- Quick wins are surfaced distinctly from deep work.
- Judgement-heavy issues are explicitly flagged for panel review before
  anyone writes code.
- Progress on large initiatives (auth, security review) is visible via real
  parent/child links, not stale markdown checklists.

See `docs/issue-taxonomy.md` for the taxonomy itself.

## Constraints learned during scoping

- **Org-level Issue Types** are not accessible via the MCP integration
  (`403 Resource not accessible by integration`). We fall back to labels
  instead of native Issue Types. This is not a meaningful loss — labels are
  strictly more queryable.
- **Projects v2 board creation** requires org-admin and is not available via
  integration tooling. Board setup is a short manual step the admin performs
  at the end; the taxonomy does not depend on it.
- **Sub-issues** are available via the integration (`sub_issue_write`). This
  is the real hierarchy mechanism.

## Phases

### Phase 0 — Scaffolding (this PR)

Lands in one PR, no issue modifications.

1. `docs/issue-taxonomy.md` — canonical reference.
2. `docs/issue-migration-plan.md` — this document.
3. `.github/ISSUE_TEMPLATE/` — structured forms for each `type:*`.
4. `.github/labels.yml` — manifest of every label in the taxonomy.
5. `.github/workflows/labels-sync.yml` — workflow that applies `labels.yml`
   to the repo on-demand. Run once manually to create the new labels.
6. `.github/workflows/agent-panel-review.yml` — the panel-review workflow,
   in dry-run mode until panelists are wired up.
7. `CLAUDE.md` — pointer to the taxonomy + dispatch contract.

### Phase 1 — Label creation

1. Merge Phase 0 PR.
2. Run `Labels Sync` workflow manually. It creates every label in
   `.github/labels.yml` with its color and description, and leaves existing
   labels alone.
3. Verify the label set in the repo.

### Phase 2 — Pilot backfill (~10 issues)

Hand-label a small sample that covers the range, to validate the taxonomy
before we apply it to 45 issues.

Suggested pilot set:

- `#16` (auth) — `type:security, complexity:complex, effort:l, judgement:design, panel-review, wave:1`
- `#17` (CORS) — `type:security, complexity:routine, effort:s, judgement:objective, wave:1`
- `#36` (floating deps) — `type:chore, complexity:trivial, effort:xs, judgement:objective, quick-win`
- `#46` (log injection) — `type:security, complexity:routine, effort:s, judgement:objective`
- `#50` (SPEC docs) — `type:docs, complexity:trivial, effort:s, judgement:preference`
- `#51` (frontend a11y) — `type:task, complexity:complex, effort:l, judgement:preference`
- `#54` (low-severity misc) — `type:chore, complexity:trivial, effort:s, judgement:objective, quick-win`
- `#55` (tracking) — `type:epic`
- `#58` (keepalive diagnostics) — `type:bug, complexity:routine, effort:m, judgement:objective`
- `#63` (auth epic) — `type:epic`

Criteria for the pilot succeeding:

- Every sampled issue cleanly fits the taxonomy without needing a new field
  or value.
- An agent simulation (e.g. "pick 3 quick wins for tier-1") returns a
  correct set.
- No two reviewers disagree strongly on a label choice for the same issue.

### Phase 3 — Sub-issue conversion for #55 and #63

Convert the existing markdown checklists in `#55` and `#63` into real
parent/child sub-issue links. `#55` already references #16–#54 in its body;
those are the children. `#63`'s body enumerates ~20 new child tickets to
file as part of the auth epic.

This is two operations:

- For existing child issues that already exist (`#55` case): use the
  `sub_issue_write` API to link them to the parent. No issue edits needed.
- For proposed children that do not yet exist (`#63` case): file each
  child, then link it.

### Phase 4 — Bulk backfill

The full manifest is already written to
[`docs/issue-taxonomy-backfill.yml`](issue-taxonomy-backfill.yml). It
classifies all 45 open issues (plus the two tracking epics added during
this migration) across the required taxonomy dimensions and declares the
sub-issue children for tracking issue #55.

Application is a single workflow invocation:

1. `Labels Sync` workflow (`workflow_dispatch`) creates the label set
   from `.github/labels.yml` — prerequisite for step 2.
2. `Issue Taxonomy Backfill` workflow (`workflow_dispatch`) reads the
   manifest and:
   - Adds the listed labels to each issue (idempotent; existing labels
     untouched).
   - Adds native sub-issue links via GraphQL for every
     parent → child edge declared in the manifest.
   - Supports `dry_run=true` (default) so the planned changes can be
     reviewed in the Action log before anything is applied.

If any classification is wrong, edit the manifest and re-run the workflow.
Labels are only **added**, never removed, so bad choices are cheap to
correct.

### Phase 4 review path (if you want human eyes before apply)

- Run `Issue Taxonomy Backfill` with `dry_run=true` — no mutations. The
  run log prints every `WOULD ADD` / `WOULD LINK` decision.
- Sanity-check the log. If needed, edit `docs/issue-taxonomy-backfill.yml`
  and re-run dry-run.
- Re-run with `dry_run=false` when the plan looks right.

### Bootstrap (first run only)

When PR #64 merges, `Labels Sync` auto-runs because `.github/labels.yml`
is in its `paths` filter — every taxonomy label including the
`rollout:*` family is created within ~30s of the merge. After that, the
label-triggered rollout works as documented.

If you want to be paranoid (or run before the auto-sync completes),
trigger `Taxonomy Rollout` via `workflow_dispatch` with `stage=preview`
— its first step is a labels sync, so it bootstraps itself even if no
labels exist yet.

### Label-triggered rollout (recommended)

For humans who would rather flip a switch than click through
workflow-dispatch forms, the rollout is label-driven end-to-end. The
orchestrator is `.github/workflows/taxonomy-rollout.yml`:

1. Apply the **`rollout:taxonomy`** label to the migration epic (#65).
2. The orchestrator fires automatically:
   - swaps the trigger label for `rollout:in-progress`
   - creates / updates labels from `.github/labels.yml`
   - runs the backfill in **dry-run** mode
   - posts the planned diff as a comment on the epic
3. Review the preview comment.
4. If it looks right, apply **`rollout:taxonomy-apply`** to the same
   epic. The orchestrator fires again and:
   - runs the backfill for real (labels + sub-issue edges)
   - posts a completion summary table
   - sets `rollout:done` and removes the in-progress/apply labels

If the preview is wrong, do not apply. Edit the manifest, remove
`rollout:in-progress`, and restart by re-applying `rollout:taxonomy`.

The whole chain is idempotent — re-running never duplicates labels or
sub-issue edges, and the manifest is the single source of truth. Manual
`workflow_dispatch` of `Labels Sync` and `Issue Taxonomy Backfill`
remains available as an admin escape hatch.

### Phase 5 — Projects v2 board (optional)

A human org-admin creates one board:

- Source: all open issues in this repo.
- Columns: Status (built-in).
- Custom fields (pull from label values):
  - `Complexity` (single-select, values from `complexity:*`)
  - `Effort` (single-select, values from `effort:*`)
  - `Judgement` (single-select, values from `judgement:*`)
  - `Wave` (single-select)
  - `Quick Win` (boolean, derived from `quick-win` label)
- Saved views:
  - "Quick wins" — filter `Quick Win = true AND Judgement = objective`
  - "Needs panel" — filter `label:panel-review`
  - "Wave 1" — filter `Wave = 1`
  - "By domain" — group-by skill-domain label

The board is pure overlay: every field is derivable from labels, so the
board going missing does not break anything.

### Phase 6 — Dashboard integration (separate PR, later)

Add a backend endpoint `GET /api/issues/dispatchable?tier=<n>&domain=<d>`
that returns a ranked list of pickable issues per the dispatch contract in
`docs/issue-taxonomy.md`. The dashboard UI surfaces this as a panel where
operators (or agents, via token) can see what's available to work on.

This is a feature, not a migration step, so it lives in its own issue/PR.

### Phase 7 — CI enforcement (separate PR, later)

Add a CI check that fails if a new non-`type:epic` issue is created without
the required labels. Deferred until after the backfill so we don't block on
our own legacy issues.

## Risk controls

- **Nothing in Phase 0 modifies any existing issue.** The PR is pure
  addition: docs, templates, workflows, and a label manifest. Merging it is
  reversible.
- **Phase 1 (label creation) is additive.** Existing labels are untouched.
- **Phase 2 (pilot) is 10 issues, each reviewable independently.**
- **Phase 3 (sub-issue conversion) does not change issue bodies or labels
  — it only adds parent/child relationships.** If we get it wrong, we
  unlink.
- **Phase 4 (bulk backfill) should gate on human approval.** The
  orchestrator proposes labels; a human (or the agent that filed the
  issue) confirms.

## What changes for agents currently working in the repo

- **In the short term: nothing.** Phase 0 adds docs and templates; it does
  not change any issue agents are currently picking up.
- **After Phase 2:** agents that look at labels get strictly more signal.
  Agents that ignore labels are unaffected.
- **After Phase 4:** agents can self-dispatch from the quick-win lane or
  by complexity tier. Redundant-pick rate should drop. Agent workflows
  (`Jules-Control-Tower.yml`, `Jules-Auto-Repair.yml`,
  `Agent-Redundant-PR-Closer.yml`, `Agent-Lease-Reaper.yml`) do not need
  edits — they already key off issue state and `claim:*` labels, which
  are unchanged. They can be upgraded to use the new labels later.
- **The existing `lease:` comment protocol and `claim:*` labels are
  untouched.** Orthogonal mechanisms; they coexist.

## Success criteria

1. Every open issue carries a `type:*`, `complexity:*`, `effort:*`, and
   `judgement:*` label.
2. Every epic has real sub-issue children, not a markdown checklist.
3. An agent asking "what should I work on?" can get a machine-readable
   answer without reading issue bodies.
4. At least one issue has gone through a full panel-review cycle
   end-to-end.
5. `docs/issue-taxonomy.md` is the cited reference in CLAUDE.md and
   onboarding for new agents.
