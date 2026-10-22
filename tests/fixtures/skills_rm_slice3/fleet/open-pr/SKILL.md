---
name: fleet-open-pr
version: 1.0.0
description: Open a fleet pull request with fragments, handoff, and squash auto-merge.
roles: [issue-remediator, pr-remediator, project-steward, barb]
repos: ["*"]
task_types: [open-pr, staff-dispatch]
---
Use change fragments, PR Handoff, ready (non-draft) PRs, arm squash auto-merge, stay under 500 lines.
