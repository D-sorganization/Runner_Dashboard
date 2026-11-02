# Issue spec supplement: #3704 — Cutover tracking (16 Grok roles)

GitHub issue #3704 is **closed**; this document is the lane-facing spec. Sync summary to #3704 as a comment when issue write access is available.

## Goal

For each listed Grok role that already has `staff/roles/<role>.yml` in RM, complete RD-side persona runtime, shadow comparison, cutover, dormant Grok fallback, and Barb relay pairing.

## Per-role checklist

For **research-scout, pragmatic-programmer, cartographer, night-watch, mad-scientist, oss-scout, board-secretary, fleet-critic, pr-remediator, issue-remediator, vision-quest, librarian, sanitation, disciple, fleet-curator** (Fleet Status folded into fleet-curator; usage-tracker retired; orchestrator folded into barb):

- [ ] Prompt/instructions synced with Grok rules
- [ ] Grok routine schedule mapped to RD cron in persona module
- [ ] 3-weekday shadow: RD thread only; Barb diffs vs Grok
- [ ] Cutover: RD canonical; Grok routine disabled except dormant fallback check
- [ ] Grok fallback dormant; `role_unhealthy` wake; takeover/back notes + ledger skip
- [ ] `RD_<ROLE>_RELAY_ALLOWLIST` for Grok counterpart (#3705)

## RD evidence on main

Persona modules + unit tests exist under `backend/staff/*_persona.py` and `tests/unit/test_*_persona.py` for the roles above plus bob, PM-*, fleet-dispatcher, expert-panel.

## Remaining work

Live shadow/cutover sign-off per role (deferred validation). **Do not** pick #3704 for a single code PR—pick unblocked children (#3700/#3702 blocked) or labeled fix/tasks.
