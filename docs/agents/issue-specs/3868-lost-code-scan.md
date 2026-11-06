# Issue spec: #3868 — Lost Code Finder scan harness

## Problem

Need tier-1 (zero-token) and tier-2 scans that **write only** to the #3867 ledger.

## Scope

**In:** Tier 1: Python vulture, import-graph; TS/JS knip/ts-prune where applicable. Tier 2: Cartographer cross-check (`backend/knowledge_pack/cartographer.py`), git blame/log/-S, issue/PR linkage.

**Out:** Tier 3 token spend; repos beyond pilot (#3869).

## Files likely touched

- `backend/staff/lost_code_scan.py`, `scripts/lost_code_scan.py`
- `tests/unit/test_lost_code_scan.py`

Depends on #3867 ledger merged.

## Acceptance criteria

- [ ] Scan writes ledger rows only; no direct issue filing.
- [ ] Tier 1 runs with no external API keys (test).
- [ ] PR &lt;500 lines, TDD; `Closes #3868`.

## PR split

| PR | Content |
| --- | --- |
| 1 | Tier-1 Python |
| 2 | Tier-1 TS + tier-2 git/cartographer |

## Labels

`type:task`, `complexity:deep`, `effort:l`, `judgement:design`, `panel-review`, `tier:strong`
