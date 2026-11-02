Slice toward blocked #2785.

## Problem

Job-board claim/release history lives in the activity log but is not exposed read-only for operators debugging lane claims.

## Scope

**In:** `GET /api/coordination/jobs/releases` (or named in #2785) projecting release/expiry/conflict rows from existing activity log.

**Out:** Heartbeat TTL (S4 remainder of #2785); writing claims.

## Files likely touched

- `backend/coordination/jobs.py` or new `backend/coordination/job_releases.py`
- `backend/activity/` projection helpers
- `tests/api/test_coordination_jobs_releases.py`
- `docs/coordination-api.md`

## Acceptance criteria

- [ ] GET returns JSON list with repo, issue, agent, reason, timestamp fields aligned to log schema.
- [ ] Read-only; no mutation endpoints in this slice.
- [ ] PR &lt;500 lines, TDD; `Closes #3856`.

## Test plan (TDD)

1. Red: fixture activity log → endpoint returns expected rows.
2. Red: empty log → empty list.
3. Green: implement projection.

## PR split

Single PR (&lt;500 lines).
