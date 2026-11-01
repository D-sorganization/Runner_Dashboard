---
issue: 3720
summary: "Harden the service-worker caching policy: cache-first storage now respects an origin's Cache-Control no-store/private signal, so static-looking paths carrying per-user or stale operational data are never written to Cache Storage."
---
