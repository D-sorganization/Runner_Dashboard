---
issue: 3814
summary: "Ensure tailscale serve exposes loopback-bound hubs to fleet peers without binding 0.0.0.0"
---

Adds `deploy/hub_tailnet_serve.py`, systemd/deploy/self-heal wiring, and docs so native Linux hubs with `DASHBOARD_LOOPBACK_AUTH=1` stay reachable on the tailnet after #3744.
