# Fleet Barb scoped leases

Hub routes live under `/api/v1/staff/barb/leases`.

| Action | Route | Notes |
| --- | --- | --- |
| Checkout | `POST …/checkout` | Default TTL 4h; cap 24h |
| Heartbeat | `POST …/heartbeat` | Requires `epoch` |
| Renew | `POST …/renew` | One call; no epoch; renews or re-acquires when expired |
| Handoff | `POST …/handoff` | Turnover + release, transfer (`next_holder`), or takeover (`takeover` + `reason`) |
| List | `GET …/leases` | `include_expired=true` (alias of `include_released`) |

CLI (`clients/fleet/barb_ops.py`): `renew`, `heartbeat`, `handoff-lease`. Client helpers:
`barb_seat_renew`, `barb_seat_handoff_lease` on `FleetClient`.

Fenced writes on `barb.turnover`, `barb.inventory`, and task `barb.handoff` slide the lease
when less than one hour remains (extend toward another 4h default window).
