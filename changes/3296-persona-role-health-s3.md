---
issue: 3296
summary: "Add persona role heartbeat, aggregate GET /api/v1/staff/roles/health with 2-reading hysteresis, and role_unhealthy/resume envelopes (#3295 S3, #3296)."
---

Implements Slice S3 of #3295 (unblocking #3296 Bob port): adds `backend/staff/role_health.py` (`RoleHealthStore`, `build_role_unhealthy_envelope`, `build_role_resume_envelope`, `notify_role_transition`) and mounts `GET /api/v1/staff/roles/health` and `POST /api/v1/staff/roles/{role}/heartbeat` on `backend/routers/staff_rd_barb.py` with fail-closed `degraded` unknown state and 2-reading hysteresis in both directions.
