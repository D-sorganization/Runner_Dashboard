Part of #3695 (epic #3295).

## Problem

George is a Grok Bot persona under #3695 with **no** `staff/roles/george.yml` in Repository_Management and **no** RD persona runtime. Prior agents could not find George's Grok routine schedule or duty statement in RD/RM docs. Implementing without Barb/Dieter input would guess cron and permissions.

## Goal

Port **George** to a persistent RD persona role (Opus 5.5 + subscription-CLI failover, RD cron, Barb-only messaging, dormant Grok fallback) using the same cutover contract as Bob (#3296) and Fleet-Dispatcher (#3701)—**after** scope is confirmed.

## Blocked (do not implement until cleared)

Grok Barb or Dieter must comment here with:

1. George's **Grok routine name(s)**, cron, and timezone.
2. **One-sentence duty** and whether George is **report-only** (like Bob) or may dispatch/lease.
3. **Public** RM role file vs **private overlay** seat (#3094).

Keep `blocked` + `judgement:design` until then.

## Scope

**In (once unblocked):** RM `staff/roles/george.yml`; RD `backend/staff/george_persona.py` + `tests/unit/test_george_persona.py`; shadow/cutover/fallback per `docs/operations/persona-cutover.md`; `RD_GEORGE_*` notify config; relay allowlist (#3705).

**Out:** Inventing duties/schedule; other persona ports; pay-per-use APIs; Sidekick packaging (GM #5478 hold).

## Files likely touched

- `backend/staff/george_persona.py` (new), `tests/unit/test_george_persona.py` (new)
- Reuse: `role_spec`, `role_capsule`, `persona_fallback_lease`, `role_health`, `scripts/rd_barb_notify.py`
- RM: `staff/roles/george.yml` (may be separate RM PR)
- Docs: `docs/operations/persona-cutover.md`

Reference: `backend/staff/fleet_dispatcher_persona.py`, `bob_persona.py`, `expert_panel_persona.py`.

## Acceptance criteria

- [ ] Blocker cleared: confirmed schedule + duty comment on this issue.
- [ ] `george.yml` passes `staff/schema.json`; loads in RD roster (test).
- [ ] Shadow posts to RD thread only; no Dieter webhook (test).
- [ ] Shadow thread failure → `ok: false` (#3792 contract).
- [ ] Fallback drill: one takeover note, ledger skip on resume, one back note (test with fakes).
- [ ] 3-weekday live shadow vs Grok (deferred validation).
- [ ] PR(s) each &lt;500 lines, TDD; exactly one `Closes #3700` on final merge.

## Test plan (TDD)

1. Red: schema + report-only permissions test before module exists.
2. Red: shadow slot → thread only, no webhook.
3. Red: shadow transport failure fails closed.
4. Red: fallback drill takeover/resume (mirror bob/fleet-dispatcher tests).
5. Green: `pytest tests/unit/test_george_persona.py -q`.

## Suggested PR split (&lt;500 lines each)

| PR | Content |
| --- | --- |
| 1 | `george_persona.py` role_data + contract tests |
| 2 | Shadow runner + notify integration |
| 3 | Fallback drill + cutover doc + relay env → **`Closes #3700`** |
| RM | `staff/roles/george.yml` in Repository_Management |

Canonical copy: `docs/agents/issue-specs/3700-george-persona-port.md`.
