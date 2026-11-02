Part of #3695 (epic #3295).

## Problem

**Linus** (open-source scout) runs only in Grok Bot. `docs/agents/role-seats.md`: *Linus stays in Grok Bot until Dieter decides scope*—upstream work reaches people outside the fleet. No RM `staff/roles/linus.yml` or RD runtime exists.

## Goal

After Dieter's scope ruling, port Linus to RD (Opus 5.5 + CLI failover, RD schedule, Barb messaging, dormant Grok fallback)—**report-only by default** unless the ruling expands permissions.

## Blocked (do not implement until cleared)

Dieter must comment here with:

1. **Allowed actions:** report-only scan vs patches vs public PRs on external repos.
2. **Repo boundaries** Linus may reference (no GM private content in public issues).
3. **Grok Linus cron** to map into RD (`America/Los_Angeles`).

Keep `blocked` + `judgement:design` until then.

## Scope

**In (once unblocked):** RM `staff/roles/linus.yml`; RD `backend/staff/linus_persona.py` + tests; Barb relay to Grok `linus` (#3705); shadow/cutover/fallback per `docs/operations/persona-cutover.md`.

**Out:** External git writes without ruling; Sidekick packaging (GM #5478); other persona ports.

## Files likely touched

- `backend/staff/linus_persona.py` (new), `tests/unit/test_linus_persona.py` (new)
- RM `staff/roles/linus.yml`
- `docs/agents/role-seats.md`, `docs/operations/persona-cutover.md`

Reference: `research_scout_persona.py`, bob/expert-panel fallback patterns.

## Acceptance criteria

- [ ] Dieter ruling comment: scope + schedule on this issue.
- [ ] `linus.yml` validates and loads (test).
- [ ] Run remains report-only unless ruling expands (test).
- [ ] Shadow transport fails closed (#3792); fallback matches Bob/#3703.
- [ ] PR(s) &lt;500 lines, TDD; `Closes #3702`.

## Test plan (TDD)

1. Red: report-only permissions test.
2. Red: run entrypoint cannot open PR/merge.
3. Red: shadow + fallback tests (scout/bob patterns).
4. Green: `pytest tests/unit/test_linus_persona.py -q`.

## Suggested PR split

| PR | Content |
| --- | --- |
| 1 | Role data + schema tests |
| 2 | Shadow + notify tests |
| 3 | Fallback + relay → **`Closes #3702`** |

Canonical copy: `docs/agents/issue-specs/3702-linus-persona-port.md`.
