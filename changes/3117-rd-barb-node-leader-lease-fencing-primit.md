---
issue: 3117
summary: "rd-barb node-leader lease + fencing primitive (slice S1): config schema for leader/standby/forwarder roles, thin lease wrapper reusing the existing scoped-lease fence; no auto-promotion yet"
branch: "claude/3117-leader-lease-s1"
dl_state: in_review
next_step: "Slice S2 (heartbeat-lapse detection + promotion) is pending an ask-barb decision on amending docs/adr/0008-barb-single-authority.md, which currently makes failover manual by binding owner ruling; #3117's body is newer and reads as superseding it, but that needs an explicit sign-off before code auto-promotes. S3 (per-node forwarder) and S4 (alert dedup) follow after S2."
---

One active node-leader at a time and a stale leader's writes refused, by giving the node-leader role its own lease scope (`barb:node-leader`) on the already-existing scoped-lease fence (`barb_lease_fence.py` / `barb_continuity.checkout_lease`/`heartbeat_lease`/`release_lease`) instead of a second mechanism. New `config/rd_barb_node_roles.json` names exactly one leader (hub-a), at most one standby (node-a), and forwarder-only nodes (node-c, node-b); `backend/staff/barb_node_leader.py` validates that schema and wraps the lease calls in node-shaped terms. No systemd/deploy changes and no automatic promotion in this slice.
