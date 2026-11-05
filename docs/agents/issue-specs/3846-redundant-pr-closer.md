# #3846 — Redundant PR closer duplicate evidence

## Problem

Agent Redundant PR Closer closed distinct lane PRs that merely shared one `Closes #N` link.

## Acceptance

1. Group by agent + **full** closing-issue set; never close across agents on shared issue alone.
2. Close only when **head ref or SHA** matches ( provable duplicate ).
3. Comment cites kept PR, issues, and head evidence.
4. Workflow comment documents policy.

## Open PR

- [#3847](https://github.com/D-sorganization/Runner_Dashboard/pull/3847)
