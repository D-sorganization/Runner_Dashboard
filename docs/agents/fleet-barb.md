# Fleet Barb scoped leases

Hub routes live under `/api/v1/staff/barb/leases`.

| Action | Route | Notes |
| --- | --- | --- |
| Checkout | `POST …/checkout` | Default TTL 4h; cap 24h |
| Heartbeat | `POST …/heartbeat` | Requires `epoch` |
| Renew | `POST …/renew` | One call; no epoch; renews or re-acquires when expired |
| Handoff | `POST …/handoff` | Turnover + push release/transfer, or pull takeover |
| List | `GET …/leases` | `include_expired=true` (alias of `include_released`) |

## Handoff modes

- **Push** (default): caller must hold the live lease. Writes turnover as the authenticated holder, then
  releases or transfers to `next_holder`. Operators (`staff.admin`) cannot push-displace another Barb; use pull.
- **Pull**: `takeover=true` or `mode="pull"` with a non-empty `reason`. Requires a Barb seat or `staff.admin`.
  Contested displacement is audited as `barb.lease.handoff_takeover` with `requested_by`, `turnover_author`,
  and `displaced_holder` when applicable.

`push_transfer` sets the incoming holder's `machine` from optional `next_machine` only (it does not copy the
departing holder's machine).

CLI (`clients/fleet/barb_ops.py`): `renew`, `heartbeat`, `handoff-lease`. Client helpers:
`barb_seat_renew`, `barb_seat_handoff_lease` on `FleetClient`.

Fenced writes on `barb.turnover`, `barb.inventory`, and task `barb.handoff` slide the lease when remaining
TTL is strictly under one hour (extend toward another 4h default window).
