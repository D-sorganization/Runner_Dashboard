# Standalone profile (no Repository_Management checkout)

Issue #3180. The dashboard works without a sibling `Repository_Management`
checkout: fleet coordination (claims, leases, priorities, role sync) is an
**optional integration**, not a hard dependency.

## What changes without it

- `backend/staff/workspace.py`'s `rm_root()` returns `None` when no
  `Repository_Management`/`Repository_Management-main` checkout is found
  under `STAFF_RM_ROOT`, `STAFF_REPOS_ROOT`, or the default repo roots.
- Code paths that read fleet facts (priorities, leases, role bundles) degrade
  to an empty/disabled state using `priorities.service.NO_RM_REASON` instead
  of erroring.
- `GET /api/diagnostics` reports the integration's availability under
  `integrations.repository_management` (`backend/integration_health.py`),
  so an operator can see at a glance why fleet-only features are absent.

## Verifying it locally

```bash
STAFF_RM_ROOT= STAFF_REPOS_ROOT=/tmp/empty pytest tests/test_standalone_smoke.py
```

`tests/test_standalone_smoke.py` boots the real FastAPI app with no
reachable `Repository_Management` checkout and asserts `/health` and
`/api/diagnostics` both respond `200` rather than erroring. The
`Standalone Smoke (no Repository_Management)` GitHub Actions workflow
(`.github/workflows/ci-standalone-smoke.yml`) runs the same suite on every
push and PR to `main`; it is not a required check, since it is a scoped
smoke pass rather than a replacement for the full backend test matrix.

## Still to do

Fleet-only UI surfaces that don't yet show an explanatory empty state, and a
unified single-flag "standalone profile" toggle (today it's inferred from
`rm_root() is None` wherever each call site happens to check it), are tracked
as follow-up slices under #3180.
