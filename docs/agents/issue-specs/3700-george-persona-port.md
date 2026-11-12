# Issue spec: #3700 — Port George to RD persona role

Canonical spec for lanes and Barb review (synced to GitHub issue body).

Parent epic: #3695 / #3295

## Problem

George is a Grok Bot persona listed under epic #3695 but has **no** `staff/roles/george.yml` in Repository_Management and **no** RD persona runtime. Without a confirmed duty statement and Grok routine schedule, prior agents would guess George's cron and permissions. The fleet needs a written spec and the same cutover contract as Bob (#3296) and Fleet-Dispatcher (#3701) once Barb/Dieter confirm scope.

## Goal (one sentence)

Port **George** to a persistent RD persona role on Opus 5.5 with subscription-CLI failover, RD cron scheduling, Barb-only user messaging, and a dormant Grok Bot fallback—**without inventing duties** Grok Barb has not confirmed.

## Context

- Avatar: `staff/avatars/personas/george.webp`. George is **not** in the personal Grok persona set (#3695).
- Reference implementations: `backend/staff/bob_persona.py`, `backend/staff/fleet_dispatcher_persona.py`, `backend/staff/expert_panel_persona.py` (#3703).
- Runbook: `docs/operations/persona-cutover.md`.

## Blocked (must clear before implementation PR)

Grok Barb or Dieter must comment on #3700 with:

1. George's **Grok routine name(s)**, cron expression(s), and timezone.
2. A **one-sentence duty statement** and whether George is **report-only** (like Bob) or may dispatch/lease.
3. Whether George is a **public** RM role file or a **private overlay** seat (#3094).

Keep labels `blocked`, `judgement:design`, and `panel-review` until the ruling; then relabel to `judgement:objective` and remove `blocked`.

## Scope (once unblocked)

**In scope**

- RM `staff/roles/george.yml` (or private overlay per ruling): persona, schedule, scope, permissions (`reports_to: barb`, Opus 5.5 default failover chain as #3296), `code_read_only: true` unless Barb explicitly allows dispatch.
- RD `backend/staff/george_persona.py` + `tests/unit/test_george_persona.py` (embedded canonical YAML, capsule schema, shadow slot, fallback drill, `persona_notify` via `RD_GEORGE_*` env).
- Barb relay: `RD_GEORGE_RELAY_ALLOWLIST` when Grok counterpart slug is confirmed (#3705).
- Shadow/cutover/fallback per `docs/operations/persona-cutover.md` (3-weekday shadow on RD thread only; dormant Grok fallback on `role_unhealthy`).

**Out of scope**

- Inventing George's duties or schedule before the blocker comment above.
- Other persona ports in the same PR.
- Pay-per-use API keys; Sidekick packaging (GM #5478 hold).

## Files likely touched

| Area | Paths |
| --- | --- |
| RD persona | `backend/staff/george_persona.py` (new) |
| RD tests | `tests/unit/test_george_persona.py` (new) |
| Shared persona infra | `backend/staff/role_spec.py`, `backend/staff/role_capsule.py`, `backend/staff/persona_fallback_lease.py`, `backend/staff/role_health.py` (reuse only) |
| Notify / shadow | `scripts/rd_barb_notify.py` (reuse; honor #3792 fail-closed shadow transport) |
| RM role file | `Repository_Management/staff/roles/george.yml` (separate RM PR if required) |
| Docs | `docs/operations/persona-cutover.md` (George subsection) |

## Acceptance criteria

- [ ] Blocker cleared: Barb/Dieter comment on #3700 links confirmed schedule + duty statement.
- [ ] `george.yml` validates against `staff/schema.json` and loads in RD roster (unit test).
- [ ] Shadow run posts to `RD_GEORGE_THREAD_ID` only; no Dieter webhook (unit test).
- [ ] Failed shadow thread transport returns `ok: false` (#3792 contract).
- [ ] Fallback drill with fakes: one takeover note, ledger skip on resume, one back note (unit test).
- [ ] 3 consecutive weekdays on RD with Grok George routines disabled except dormant check (deferred validation).
- [ ] Cohesive fleet PRs (~500 lines advisory reference), TDD, exactly one `Closes #3700` on the final merge.

## Test plan (TDD)

1. **Red:** `test_george_role_data_matches_schema_and_report_only_default` — permissions and failover chain before implementation module exists.
2. **Red:** `test_george_shadow_slot_posts_thread_not_webhook` — fake notify; assert `status=shadow` and zero webhook calls.
3. **Red:** `test_george_shadow_thread_failure_fails_closed` — injected transport error → `ok: false`.
4. **Red:** `test_george_fallback_drill_takeover_and_resume_notes` — mirror `tests/unit/test_bob_persona.py` / `test_fleet_dispatcher_persona.py` drill pattern.
5. **Green:** implement `george_persona.py` until unit suite passes; run `pytest tests/unit/test_george_persona.py -q`.
6. **Gate:** `pytest tests/ -q --ignore=tests/deploy` (or targeted staff tests) before PR.

## Suggested PR split (advisory ~500 lines each (not a gate))

| PR | Content | Closes |
| --- | --- | --- |
| 1 | `george_persona.py` role_data, schedule constants, capsule schema + contract unit tests | — (stack) |
| 2 | Shadow runner + notify integration tests (#3792 semantics) | — |
| 3 | Fallback drill + cutover doc snippet + relay env wiring | `Closes #3700` |
| RM | `staff/roles/george.yml` only in Repository_Management | RM tracking issue if any |

## Recommended labels

`type:task`, `complexity:deep`, `effort:m`, `judgement:design`, `panel-review`, `tier:strong`, `domain:staff`, `blocked` (until ruling)
