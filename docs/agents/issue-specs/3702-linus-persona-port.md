# Issue spec: #3702 — Port Linus to RD persona role

Canonical spec for lanes and Barb review. Paste or sync to GitHub issue #3702 when issue write access is available.

Parent epic: #3695 / #3295

## Goal (one sentence)

When Dieter approves scope, port **Linus** (open-source contribution scout Grok persona) to RD with Opus 5.5 + CLI failover, RD schedule, Barb messaging, and dormant Grok fallback—without upstream writes until scope is explicit.

## Context

- `docs/agents/role-seats.md`: **“Linus … Stays in Grok Bot for now: upstream contributions reach people outside the fleet, so Dieter decides scope first.”**
- No RM `staff/roles/linus.yml` today. Avatar: `staff/avatars/personas/linus.webp`.

## Blocked (must clear before implementation PR)

Dieter must rule on #3702:

1. **Allowed actions**: report-only opportunity scan vs drafting patches vs opening public PRs on external repos.
2. **Repos and boundaries**: which upstream orgs/repos Linus may touch; GM private content stays out of public issues.
3. **Schedule**: Grok Linus routine cron to map into RD.

## Scope (once unblocked)

**In**

- RM `staff/roles/linus.yml`: report-only default, `reports_to: barb`, failover chain as #3296.
- RD `backend/staff/linus_persona.py` + tests.
- Barb relay allowlist to Grok counterpart `linus` (#3705).
- Shadow/cutover/fallback per `docs/operations/persona-cutover.md`.

**Out**

- External contribution without explicit permission in the ruling.
- Sidekick packaging (GM #5478 hold).

## Acceptance criteria

- [ ] Dieter ruling comment: scope + schedule on #3702.
- [ ] `linus.yml` validates and loads (test).
- [ ] Scheduled run is report-only unless ruling expands (test).
- [ ] Shadow + fallback match Bob/#3703; shadow transport failures fail closed (#3792).
- [ ] One PR `<500` lines, TDD, `Closes #3702`.

## Recommended labels

`type:task`, `complexity:deep`, `effort:m`, `judgement:design`, `panel-review`, `tier:strong`, `domain:staff`
