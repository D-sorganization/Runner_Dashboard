---
issue: 3064
summary: "Unified Barb role preset (parity with agent-grok) and Barb seat detection for all lane principals; Dot's Barb = agent-codex-hands"
---

Desk hub: after merge, wait for auto-deploy (~10 min, main CI green) or restart runner-dashboard to load merged defaults; verify GET /api/fleet/status auto_deploy SHA. No new token mint unless a Barb lane still uses a principal row without bot+barb roles in principals.yml (admin: reload from repo defaults or re-save principal).
