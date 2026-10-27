---
issue: 2911
summary: "Barb seat leases: release keeps the row so the fencing epoch stays monotonic per scope; bad scope returns 422 and scope is normalised for checkout/heartbeat/release; release validates the holder before writing turnover, atomically."
---
