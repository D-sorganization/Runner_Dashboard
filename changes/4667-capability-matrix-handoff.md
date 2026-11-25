---
issue: 4667
summary: "Correct the tooling matrix so handoff_state, status, and surface stay distinct and Barb lease headers are required."
---

Document the store handoff map and that complete_desktop leaves surface unchanged. Record that X-Barb-Scope and X-Barb-Lease-Epoch are a separate fence from expected_epoch, and that enforce is the default.
