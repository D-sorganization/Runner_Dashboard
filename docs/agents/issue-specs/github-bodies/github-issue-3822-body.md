Board priority 11-02 item 2.

## Problem

Scheduled staff roles can miss slots (skipped, blocked, or failed runs) without a fleet-visible consecutive-miss signal. Operators need an alert after **two consecutive missed** scheduled slots for a role.

## Scope

**In:** Counter per role for consecutive missed scheduled slots; reset on successful run; debounced alert to Barb (rd-threads / persona notify pattern).

**Out:** Changing cron definitions; auto-dispatch remediation; roles without schedules.

## Files likely touched

- `backend/staff/scheduler.py` or dedicated `backend/staff/missed_slot_tracker.py`
- `backend/staff/role_health.py` (optional integration—do not duplicate unhealthy semantics)
- `tests/unit/test_missed_scheduled_role_alert.py` (new)

## Acceptance criteria

- [ ] Two consecutive missed slots increment counter; successful run resets to zero.
- [ ] Alert emitted once per streak (debounced); not on first miss alone.
- [ ] Unit tests fail-first (TDD); PR &lt;500 lines; `Closes #3822`.

## Test plan (TDD)

1. Red: `test_two_misses_emit_alert`, `test_success_resets_counter`, `test_debounce_no_duplicate_alert`.
2. Green: implement tracker + hook from scheduler tick or run completion callback.

## PR split

Single PR preferred (&lt;500 lines). If needed: (1) tracker + tests, (2) scheduler hook + alert wiring.
