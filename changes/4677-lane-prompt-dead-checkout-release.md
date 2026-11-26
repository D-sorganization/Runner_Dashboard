---
issue: 4677
summary: "Expire lane-prompt checkouts after 15 minutes without a heartbeat, and let operators force-release a live checkout without its fence"
paths: "backend/coordination/lane_prompts.py, backend/routers/coordination.py, tests/api/test_coordination_lane_prompts.py, tests/test_lane_prompts_health.py"
---

Dead CLI lanes no longer keep a lane_id locked for the full 60-minute TTL.
Checkout, catalog, and health expire an active row when `heartbeat_at` is at
least 900 seconds old, so a relaunch can claim the lane. `POST
/api/coordination/lane-prompts/force-release` requires `priorities.write`
(operator/admin) and releases a still-fresh checkout without `fence_token` or
`session_id`. Remote dead PIDs are not inspected; the heartbeat window plus
operator release covers the multi-host lockout.
