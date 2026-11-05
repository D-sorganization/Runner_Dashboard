---
issue: 3823
summary: "agent_bus reaper defers stale-session claim release until the registering node's readyz is ok (local probes or peer /readyz, once per node per pass); unresolvable nodes keep today's reap; deferrals surface as reaper deferred_sessions. Supersedes #3833."
dl_state: "in_review"
next_step: "Merge via queue; Barb closes superseded #3833"
branch: "claude/3823-reaper-readyz"
---
