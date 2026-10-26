---
issue: 2734
summary: "Barb-authored writes (dispatch, directives, turnover, inventory, handoff, shared memory) carry the scope lease epoch via X-Barb-Scope and X-Barb-Lease-Epoch and get 409 stale_barb_lease when the lease is not held, expired or at another epoch; checked under the continuity store lock with the write and audit-logged; fleet clients send the seat automatically"
---
