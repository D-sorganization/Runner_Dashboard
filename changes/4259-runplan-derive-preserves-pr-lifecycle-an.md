---
issue: 4259
summary: "RunPlan.derive preserves pr_lifecycle and pr_restrictions on retry/failover paths"
---

Merged latest main; retry and failover paths keep using ``derive_run_plan`` so
``dispatch_bundle`` and operator PR fence fields copy forward on derived plans.
