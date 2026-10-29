---
issue: 3296
summary: "Generalize the rd-barb persona runtime (notify, provider, watchdog, node-leader lease) to any --role and RD_<ROLE>_* env while keeping Barb golden-identical (#3295 S1, #3296)."
---

Slice S1 of #3295 (unblocking #3296 Bob port): generalizes `scripts/rd_barb_notify.py`, `scripts/rd_barb_provider.py`, `scripts/rd_barb_watchdog.py`, and `backend/staff/barb_node_leader.py` to accept `--role` / `role=` and `RD_<ROLE>_*` environment variables, adds `persona_notify` stamping `sender_role` and `RD_<ROLE>_SHADOW` thread-only mode, while preserving golden-identical `rd-barb` defaults.
