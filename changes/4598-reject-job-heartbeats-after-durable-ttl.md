---
issue: 4598
summary: "Reject job heartbeats after durable TTL expiry or an epoch change before renewal"
---

Job heartbeats require a matching live durable lease and current fence inside the
issue lock, both before and after remote claim lookup. Expired or swept leases
require a new acquisition. Shared presence/claim/lane TTL policy and lifecycle
metrics remain follow-up work under #4598.
