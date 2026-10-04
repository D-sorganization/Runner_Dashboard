---
issue: 1894
summary: "RM-5 collate-changes.yml folds merged changes/ fragments into the SPEC.md change log and development log through a bot PR armed by scripts/automerge_guard.py; Spec Check accepts a change fragment in place of SPEC.md; the workflow is routed to the bulk tier (tests/test_collate_changes_workflow.py, tests/test_spec_check_workflow.py). Merge the RD change-fragments PR first."
---

Workflow-only PR; depends on the vendored scripts from the change-fragments PR (Repository_Management#1894). Auto-merge is intentionally off until that PR is merged.
