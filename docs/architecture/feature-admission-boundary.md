# Feature admission and reservation boundary (RM #2278)

Board decision 2026-11-05: **accept** (2–1, dissent preserved). This document
codifies the Runner_Dashboard **source-only** consumer contract aligned with
SPEC dispatch semantics (§14 Agent Dispatch) and the sibling-repos admission
split (new capability vs operational continuation). Runtime enforcement waits
on a freshness-evidence activation PR.

## Work kinds

| `DashboardWorkKind` | Authoritative `dispatch_kind` examples | Quota |
| ------------------- | ------------------------------------ | ----- |
| `new_feature` | `implementation`, `design`, `pr`, `expand_epic_children` | Yes |
| `operational` | `ci_fix:*`, `conflict`, unknown kinds (fail-safe) | No |
| `continuation` | `continue`, `needs_input_resume`, `continuation` | No |

Classification uses **`dispatch_kind` only** (signed dispatch envelope / staff
router). It must not read Quick Dispatch `task_kind` or workflow `force` flags.

## Non-authoritative caller hints

SPEC §13 Quick Dispatch exposes `task_kind` on the request body. Those fields,
and boolean `force` hints on staff or workflow callers, are **audit metadata
only** — they never change `DashboardWorkKind` or reservation quota.

Implementation: `backend/feature_admission_boundary.py`
(`caller_controlled_hints_are_non_authoritative`).

## Reservation / freshness contract

Atomic reservations and historical reconciliation (RM #2253 family) require
**freshness evidence** before enforcement:

```json
{ "issued_at": "<ISO-8601>", "receipt_id": "<non-empty id>" }
```

Without it, `enforce_reservation_boundary` returns
`reservation_freshness_evidence_absent` and `allowed: false`.

Until `activation_enabled=True` in a follow-up PR, every evaluation remains
fail-closed with `feature_admission_boundary_not_activated`.

## Out of scope (this contract)

- Wiring into `dispatch_arm_cap`, staff scheduler, or Quick Dispatch handlers
- Changing live quota tables or SQLite reservation stores
- Enabling owner `force_claim_override` behaviour (#2562) for feature rollout

Tests: `tests/unit/test_feature_admission_boundary.py`
