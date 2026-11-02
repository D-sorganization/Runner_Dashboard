# Expert panel request format (Barbara relay, #3711 slice 2)

Cloud agents file hub expert panels by opening an issue in the private **Barbara** repo with label `panel-request`. The barb-worker **panel relay** (off by default) validates the body and calls `POST /api/v1/staff/panels` on the hub, then comments with the digest.

## Issue body

Include exactly one fenced JSON block tagged `panel-request`:

````markdown
## Panel request

```panel-request
{
  "topic": "Should we adopt X?",
  "mode": "debate",
  "rounds": 3,
  "experts": [
    {"name": "Architect", "perspective": "Maintainability"},
    {"name": "Skeptic", "perspective": "Risk"},
    {"name": "Operator", "perspective": "Cost"}
  ]
}
```
````

**Rules**

- Authors must be on the relay allowlist (owner + fleet bot logins configured on the worker).
- Never put `confirm_cost` in the JSON; the relay never confirms cost over threshold (hub returns 400 — operator must rerun via `fleetctl panel-start --confirm-cost` locally).
- One panel per issue number (deduped by relay state).
- Relay respects `BARB_PANEL_RELAY_ENABLED=0` kill switch and a daily cap.

## Cloud agent alternative (no Barbara issue)

Use `fleetctl panel-start --request '<json>'` from a tailnet host with `staff.chat` scope (#3711 slice 1).
