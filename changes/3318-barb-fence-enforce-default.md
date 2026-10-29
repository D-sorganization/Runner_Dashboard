---
issue: 3318
summary: "Barb lease fence enforce mode by default (#3279 step 2): Barb-seat writes without a current X-Barb-Scope/X-Barb-Lease-Epoch claim are rejected with 409 stale_barb_lease (reason claim_required) without emitting the duplicate #3290 warning log; DASHBOARD_BARB_FENCE_MODE=warn|off provides an instant env-var rollback switch."
---
