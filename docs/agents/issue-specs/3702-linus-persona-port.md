# Issue spec: #3702 — Port Linus to RD persona role

Canonical spec for lanes and Barb review (synced to GitHub issue body).

Parent epic: #3695 / #3295

## Problem

**Linus** (open-source contribution scout) still runs only as a Grok Bot persona. `docs/agents/role-seats.md` says Linus **stays in Grok Bot until Dieter decides scope**, because upstream contributions reach people outside the fleet. There is no RM `staff/roles/linus.yml` and no RD runtime; implementing without a scope ruling would risk unauthorized public contributions.

## Goal (one sentence)

When Dieter approves scope, port **Linus** to RD with Opus 5.5 + CLI failover, RD schedule, Barb messaging, and dormant Grok fallback—default **report-only** unless the ruling expands permissions.

## Context

- Avatar: `staff/avatars/personas/linus.webp`.
- Reference: `backend/staff/research_scout_persona.py`, `backend/staff/oss_scout` patterns (read-only scouts).
- Runbook: `docs/operations/persona-cutover.md`.

## Blocked (must clear before implementation PR)

Dieter must comment on #3702 with:

1. **Allowed actions:** report-only opportunity scan vs drafting patches vs opening public PRs on external repos.
2. **Repo boundaries:** which upstream orgs/repos Linus may reference; GM private content stays out of public issues.
3. **Schedule:** Grok Linus routine cron to map into RD (`America/Los_Angeles`).

Keep `blocked`, `judgement:design`, and `panel-review` until the ruling; then `judgement:objective` and remove `blocked`.

## Scope (once unblocked)

**In scope**

- RM `staff/roles/linus.yml`: default report-only, `reports_to: barb`, failover chain as #3296.
- RD `backend/staff/linus_persona.py` + `tests/unit/test_linus_persona.py`.
- Barb relay allowlist to Grok counterpart `linus` (#3705).
- Shadow/cutover/fallback per persona cutover runbook.

**Out of scope**

- External git writes or public PRs without explicit permission in Dieter's ruling.
- Sidekick packaging (GM #5478 hold).
- George / other persona ports.

## Files likely touched

| Area | Paths |
| --- | --- |
| RD persona | `backend/staff/linus_persona.py` (new) |
| RD tests | `tests/unit/test_linus_persona.py` (new) |
| RM role | `Repository_Management/staff/roles/linus.yml` |
| Docs | `docs/agents/role-seats.md` (update Linus row after cutover), `docs/operations/persona-cutover.md` |

## Acceptance criteria

- [ ] Dieter ruling comment: scope + schedule on #3702.
- [ ] `linus.yml` validates and loads (unit test).
- [ ] Scheduled run is report-only unless ruling expands permissions (unit test asserts `open_pr`/`merge` false).
- [ ] Shadow + fallback match Bob/#3703; shadow transport failures fail closed (#3792).
- [ ] One closing fleet PR `<500` lines (or stacked PRs below), TDD, `Closes #3702`.

## Test plan (TDD)

1. **Red:** `test_linus_role_data_report_only_permissions` — fails before module exists.
2. **Red:** `test_linus_run_does_not_open_pr_or_merge` — guard on permissions + run entrypoint.
3. **Red:** shadow + fallback tests copied from `test_research_scout_persona.py` / bob drill pattern.
4. **Green:** implement until `pytest tests/unit/test_linus_persona.py -q` passes.

## Suggested PR split (advisory ~500 lines each (not a gate))

| PR | Content | Closes |
| --- | --- | --- |
| 1 | `linus_persona.py` + role_data/schema tests | — |
| 2 | Shadow slot + notify fail-closed tests | — |
| 3 | Fallback drill + relay env + doc touch | `Closes #3702` |

## Recommended labels

`type:task`, `complexity:deep`, `effort:m`, `judgement:design`, `panel-review`, `tier:strong`, `domain:staff`, `blocked`
