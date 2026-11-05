# #3824 — Stale Antigravity login runbook

## Problem

Operators need steps when agy subscription login is stale, including signals from credential mirror health.

## Acceptance

1. **Runbook** — `docs/runbooks/` doc with detection (mirror drift, staff errors, login probe) and remediation (re-auth, sync, verify) using **example** hostnames only (topology guard).
2. **Cross-link** — Points to credential mirror health (#3820) and durable credentials runbook.
3. **No code required** if covered by #3829; doc-only PR acceptable.

## Open PR

- [#3829](https://github.com/D-sorganization/Runner_Dashboard/pull/3829)
