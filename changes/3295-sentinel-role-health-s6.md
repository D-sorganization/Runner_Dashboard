---
type: feat
scope: staff
---
Add Brick sentinel per-role health probe (`probe_roles_health`), 2-reading hysteresis monitor (`SentinelRoleHealthMonitor`) with 10-minute outside probe separation for `hub_unreachable` (`decision_authority="grok"`) vs hub-reported role failures (`decision_authority="rd"`), 4-hour backstop tracking, and isolated `role_unhealthy` push wake dispatch (#3295 S6).
