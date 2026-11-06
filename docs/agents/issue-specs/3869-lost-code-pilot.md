# Issue spec: #3869 — Lost Code Finder weekly pilot on Tools

## Problem

After #3867 ledger and #3868 harness land, schedule a **weekly** pilot on `D-sorganization/Tools` before expanding repos.

## Scope

**In:** Cron/scheduler entry, pilot config for Tools repo, Barb review routing for follow-ups (&lt;500 line unstacked PRs).

**Out:** Second repo until exit criteria met.

## Exit criteria (before second repo)

- Citations reproducible from ledger links
- UNKNOWN fail-closed verified
- All indirection check classes applied per finding

## Files likely touched

- `backend/staff/scheduler.py` or holds-safe schedule entry
- `config/` pilot allowlist
- `docs/operations/lost-code-finder-pilot.md` (new, brief)

## Acceptance criteria

- [ ] Weekly slot fires in dry-run on staging (test).
- [ ] Pilot limited to Tools (test/config).
- [ ] PR &lt;500 lines; `Closes #3869`.

## Labels

`type:chore`, `complexity:complex`, `effort:m`, `judgement:objective`, `tier:cli`
