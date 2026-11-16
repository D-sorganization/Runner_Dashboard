---
issue: 4274
summary: "Fleet-control fan-out sends the shared hub-to-node headers; a node accepts the fleet token for a local-only call (and only that); a node that refuses or is unreachable returns 502 upstream_error with per-node results; ?local=1 on a hub no longer fans out (F-05)."
---
