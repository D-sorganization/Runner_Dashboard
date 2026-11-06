---
issue: 3064
summary: "Shared barb role preset for all Barb lane principals (full operator hub surface minus staff.approve), repo bypass for Barb-dispatched staff runs, intake/PR hygiene parity, dispatcher metrics origin for all Barb seats, no assistant_kind-only Barb fence"
---

Desk hub: after merge, wait for auto-deploy (~10 min once main CI is green) so the pod picks up the new `config/agent_principals.yml` and Python; or `sudo systemctl restart runner-dashboard` on hub-a if you need it immediately. Verify `GET /api/fleet/status` `auto_deploy.deployed_sha` matches main. No token minting in-repo; Dot’s Barb hub identity remains **`agent-codex-hands`** (roles `[bot, barb]` in defaults).

Rollup (this PR): `BARB_ROLE_SCOPES` + parity test; `grok-barb` principal; Barb repo-allowlist bypass; lease takeover for any Barb seat; `require_any_scope` for PR hygiene batch; remove agent-grok-only intake approval gate; `dispatcher_origin` buckets all Barb principals as legacy `grok` for metrics; OBO dispatch fence loads subject principal from identity; `is_barb_seat` no longer treats non-Barb `grokbot` bots as Barb.
