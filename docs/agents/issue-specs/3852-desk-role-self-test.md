# #3852 — Desk core role dry-run self-test

## Problem

Operators cannot verify staff dispatch planning for core Desk roles without starting real runs.

## Acceptance

1. **`POST /api/staff/{role}/self-test`** — Dry-run dispatch plan only (`staff.dispatch` bearer).
2. **`POST /api/staff/self-test/desk-core`** — Seven default core roles (overridable via `STAFF_DESK_CORE_ROLES`); aggregated pass/fail per role.
3. **Optional** — `persona_drill=true` runs #3704 staging fallback in temp dir (shadow notify only).
4. **Tests** — API tests with fake scheduler; no live provider calls.

## Open PR

- [#3853](https://github.com/D-sorganization/Runner_Dashboard/pull/3853)
