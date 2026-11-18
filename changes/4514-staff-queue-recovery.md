---
issue: 4514
summary: "Recover workerless queued staff runs at startup, surface stale queues, and configure peer lookup budgets"
---

Startup reconciliation marks abandoned queued entries as retryable failures. The staff summary flags local queues without workers after ten minutes, respecting scheduled retry deadlines. Peer discovery accepts a positive finite timeout override and returns degraded detail for a safely identified last-known owner.
