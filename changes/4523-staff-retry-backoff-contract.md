---
issue: 4523
summary: "staff retry_policy backoff saturates at the cap on overflow, never returns negative/NaN delays, and malformed STAFF_RETRY_* env falls back instead of crashing import"
---
