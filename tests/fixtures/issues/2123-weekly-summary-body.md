## Summary

Weekly summary endpoint for Bob and board meetings: Markdown-friendly JSON combining delivery rollups with Dieter time (staff runs, proposals, manual steps) and cost per merged PR from existing staff spend data.

## Acceptance criteria

- [ ] `GET /api/v1/metrics/weekly-summary` (or documented alias) stable schema for staff consumers.
- [ ] Dieter-wait attribution from staff run records; cost from session/spend stores.
- [ ] Idempotent weekly artifact optional hook (similar to usage report pattern).
- [ ] Tests with fixture staff rows + metric rollups.
