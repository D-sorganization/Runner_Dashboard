---
issue: 2489
summary: "Barb seats: lease holder bound to caller, operator-gated audited takeover, heartbeat endpoint, TTL cap 422, required handoff epoch, memory.read for agents."
paths: "backend/routers/staff_barb.py,backend/staff/barb_continuity.py,backend/identity.py,clients/fleet/barb_ops.py"
---

No material handoff change - server-side seat hardening; lease-epoch fencing deferred to design panel.
