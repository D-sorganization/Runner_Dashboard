# Issue spec: #3867 — Lost Code Finder evidence ledger

## Problem

Lost Code Finder needs a durable **evidence ledger** contract before scans write findings. Without schema + contract tests, tier-1/2 scans cannot fail closed on UNKNOWN verdicts.

## Scope

**In:** Ledger schema (fields below), validation, contract unit tests; no scan harness (#3868).

**Out:** Pay-per-use API calls; cross-repo rollout (#3869).

## Fields (required unless noted)

`path`, `symbol`, `evidence`, `origin` (commit/PR/issue), `inferred_purpose`, optional `replacement`, `verdict` (`IMPLEMENTED`|`SAVE`|`RETIRE`|`UNKNOWN`), `confidence`, `links`, `indirection_checks` (record per class applied).

## Files likely touched

- `backend/staff/lost_code_ledger.py` or `backend/staff/lost_code/` (new package)
- `tests/unit/test_lost_code_ledger_contract.py`

## Acceptance criteria

- [ ] Schema rejects incomplete rows; UNKNOWN requires explicit indirection checklist (test).
- [ ] Cohesive PR (~500 lines advisory), TDD; `Closes #3867`.

## Test plan (TDD)

Red contract tests for each field rule → green implementation.

## PR split

Single PR.

## Labels

`type:task`, `complexity:complex`, `effort:m`, `judgement:design`, `panel-review`, `tier:strong`
