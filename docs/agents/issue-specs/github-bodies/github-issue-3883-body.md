Parent: #3861 (keep open for separate thread-region diagnostics).

## Problem

Staff Console focus tests fail on mobile drawer scenarios: hidden focus candidates and passive thread restore (Frontend Tests run 41493497589 — 3 failures on #3865 head).

## Scope

**In:** Fix focus trap / restore in mobile drawer code paths; align with a11y expectations in failing tests.

**Out:** #3861 intermittent thread-region root cause; unrelated Staff Console features.

## Files likely touched

- `frontend/src/` Staff Console / drawer components (exact paths from failing test files)
- `frontend/src/pages/__tests__/` or colocated tests from CI log

## Acceptance criteria

- [ ] Previously failing focus tests pass locally (`npm test`).
- [ ] No regression on desktop drawer focus tests.
- [ ] PR &lt;500 lines, TDD; `Closes #3883`.

## Test plan (TDD)

1. Red: reproduce failing vitest cases from CI names.
2. Green: minimal focus management fix.
3. Gate: `npm test`, `npm run typecheck`.

## PR split

Single PR (&lt;500 lines).
