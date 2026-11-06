# Issue spec supplement: #3704 — Cutover tracking (16 Grok roles)

GitHub issue **#3704 is closed**; this is the lane-facing spec. A summary comment is posted on #3704 for history.

## Problem

Epic #3695 tracked cutover of many Grok personas that **already had** RM `staff/roles/*.yml`. RD persona modules and unit tests now exist on `main`, but **live** shadow comparison, cutover sign-off, and Barb relay pairing were tracked on #3704. Lanes must not treat #3704 as a single implementation ticket—it is a checklist epic.

## Goal

Document per-role cutover evidence and remaining deferred validation; direct implementers to **open child work** (fixes, unblocked persona ports), not #3704 itself.

## Scope

**In scope (per role on checklist)**

Roles: research-scout, pragmatic-programmer, cartographer, night-watch, mad-scientist, oss-scout, board-secretary, fleet-critic, pr-remediator, issue-remediator, vision-quest, librarian, sanitation, disciple, fleet-curator (Fleet Status folded into fleet-curator; usage-tracker retired; orchestrator folded into barb).

For each:

- Prompt/instructions synced with Grok rules
- Grok routine schedule mapped to RD cron in persona module
- 3-weekday shadow (RD thread only; Barb diffs vs Grok)
- Cutover: RD canonical; Grok routine disabled except dormant fallback check
- Dormant Grok fallback on `role_unhealthy`; takeover/back notes + ledger skip
- `RD_<ROLE>_RELAY_ALLOWLIST` (#3705)

**Out of scope**

- Re-opening #3704 for a monolithic PR.
- #3700 / #3702 (blocked on Barb/Dieter scope).
- Paperclip epic #3874.

## RD evidence on main

- Persona modules: `backend/staff/*_persona.py`
- Tests: `tests/unit/test_*_persona.py`
- Also: bob, PM-*, fleet-dispatcher, expert-panel

## Acceptance criteria

- [ ] Each role row in the GitHub checklist has a comment or linked PR proving shadow/cutover **or** an explicit deferred-validation entry.
- [ ] No lane opens `Closes #3704` for routine code changes.

## Test plan

Verification is **operational**, not a single unit file:

1. For a role marked done, run `pytest tests/unit/test_<role>_persona.py -q`.
2. Confirm shadow env documented in `docs/operations/persona-cutover.md`.
3. Deferred: 3-weekday live shadow sign-off recorded in fleet deferred-validation catalog.

## PR split

Do **not** split work under #3704. Pick labeled fix/tasks (e.g. #3792) or unblocked persona children.
