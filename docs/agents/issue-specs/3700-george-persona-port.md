# Issue spec: #3700 — Port George to RD persona role

Canonical spec for lanes and Barb review. Paste or sync to GitHub issue #3700 when issue write access is available.

Parent epic: #3695 / #3295

## Goal (one sentence)

Port the Grok Bot persona **George** to a persistent RD persona role on Opus 5.5 with subscription-CLI failover, RD cron scheduling, Barb-only user messaging, and a dormant Grok Bot fallback—without inventing duties that Grok Barb has not confirmed.

## Context

- George appears in epic #3695 as a Grok Bot persona with **no** `staff/roles/george.yml` in Repository_Management today (same class as #3701 Fleet-Dispatcher and #3703 Expert Panel before their RD ports).
- Prior agents (#3700 comments) could not find George's job description or Grok routine schedule in RD/RM docs. Implementing without Barb input would guess both duties and cron, unlike PM-* siblings (explicit “project manager per repo” duty) or roles with existing RM YAML (#3704 checklist).
- Avatar: `staff/avatars/personas/george.webp` (persona gallery). Personal Grok personas stay in Grok Bot per #3695; George is **not** in that personal set.

## Blocked (must clear before implementation PR)

Grok Barb (Barbara #15 exchange) or Dieter must confirm on #3700:

1. George's **standing Grok routine name(s)**, cron expression(s), and timezone.
2. A **one-sentence duty statement** and whether George is **report-only** (like Bob) or may dispatch/lease.
3. Whether George is a **public** RM role file or a **private overlay** seat (#3094).

Until then, lanes must not implement—only spec and shadow infra prep.

## Scope (once unblocked)

**In**

- RM `staff/roles/george.yml` (or private overlay equivalent per ruling): persona, mapped schedule, scope, permissions (`reports_to: barb`, Opus 5.5 default failover chain matching #3296), `code_read_only` unless Barb explicitly allows dispatch.
- RD: `backend/staff/george_persona.py` + tests mirroring #3701/#3703 (embedded canonical YAML, capsule schema, shadow slot, fallback drill, `persona_notify` with `RD_GEORGE_*` config).
- `RD_GEORGE_RELAY_ALLOWLIST` entry when a Grok counterpart slug is confirmed (#3705).
- Runbook steps in `docs/operations/persona-cutover.md`: 3-weekday shadow (RD thread only; diff vs Grok George), cutover, dormant Grok fallback on `role_unhealthy`.

**Out**

- Inventing George's duties or schedule (this issue stays blocked until Barb/Dieter answer above).
- Other persona ports; RM YAML-only PRs may land in RM separately.

## Fallback behaviour

Same contract as Bob (#3296): unhealthy RD role → one Grok takeover note + slim fallback run from capsule; `resume` after two healthy readings → one “back on RD” note; ledger skips slots Grok ran.

## Acceptance criteria

- [ ] Blocker cleared: Barb/Dieter comment links George's confirmed schedule + duty statement on #3700.
- [ ] `george.yml` passes `staff/schema.json` and loads in RD roster (test).
- [ ] Shadow run posts to RD thread and **does not** call Dieter webhook (test).
- [ ] Shadow thread transport failure returns `ok: false` (not false success) per #3792 contract.
- [ ] Fallback drill on staging: hub stopped → exactly one takeover note, no duplicate slot after resume, one back note (test with fakes).
- [ ] 3 consecutive weekdays live on RD with zero Grok George scheduled routines besides dormant check (deferred validation).
- [ ] One PR `<500` lines, TDD, exactly one `Closes #3700`.

## Recommended labels

`type:task`, `complexity:deep`, `effort:m`, `judgement:design`, `panel-review`, `tier:strong`, `domain:staff`
