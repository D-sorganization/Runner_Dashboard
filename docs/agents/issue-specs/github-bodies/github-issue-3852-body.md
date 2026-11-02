## Problem

Seven Desk hub staff roles lack a **dry-run self-test** path. Operators cannot verify dispatch planning (and optional isolated persona fallback drills) without starting real staff runs.

## Scope

**In:** API or staff action that exercises role dispatch planning in dry-run mode (no provider invocation, no merge/push); optional persona fallback drill flag mirroring existing drill helpers.

**Out:** New roles; changing production schedules; pay-per-use APIs.

## Files likely touched

- `backend/routers/staff.py` or `backend/staff/dry_run.py` (new)
- `backend/staff/scheduler.py` (plan-only entry)
- `tests/api/test_staff_dry_run_self_test.py` (new)
- `docs/staff-hub.md` (operator note)

## Acceptance criteria

- [ ] Dry-run returns planned slots/providers without side effects (test).
- [ ] Auth: orchestrator/staff peer only (match existing staff mutation contract).
- [ ] Seven named hub roles covered (list in PR description).
- [ ] PR &lt;500 lines, TDD; `Closes #3852`.

## Test plan (TDD)

1. Red: POST dry-run → 200 with plan payload, assert no run store mutation.
2. Red: unauthorized principal → 403.
3. Green: implement endpoint.

## PR split

| PR | Content |
| --- | --- |
| 1 | Core dry-run planner + tests |
| 2 | Per-role wiring if &gt;500 lines |
