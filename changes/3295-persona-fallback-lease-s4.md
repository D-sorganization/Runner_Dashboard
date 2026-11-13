---
category: feat
---
- **Grok Bot offload S4 (#3295, #3296)**: Add `persona:<role>` fallback lease with monotonic fencing (`backend/staff/persona_fallback_lease.py`), `(role, job, slot)` idempotency ledger, and offline fallback spool ingest on top of `BarbContinuityStore`.
