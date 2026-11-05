# #3820 — Stale provider credential mirrors

## Problem

WSL/Windows copies of provider login material can lag the winning sync source, causing silent auth drift.

## Acceptance

1. **Audit** — `deploy/sync_provider_credentials.py` (or sibling) compares mirror mtimes/content hash to source without logging secrets.
2. **Surface** — `provider_auth` preflight and staff summary `attention` include `failure_class: credential_mirror_stale` with mirror path metadata (no secret values).
3. **Tests** — Unit tests for stale/equal/missing mirror cases.

## Open PR

- [#3829](https://github.com/D-sorganization/Runner_Dashboard/pull/3829) (also #3824/#3825 runbooks)
