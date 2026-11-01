---
issue: 3705
summary: "Add agent-to-agent relay (kind=relay) in rd_barb_notify/persona_notify: allowlisted role-pair forwarding to a named Grok Bot counterpart via Barb, personal-persona hard exclusion with the Feynman/Karl exception, per-thread idempotent delivery and an audit hook. Part of epic #3695 (Grok Bot role port); staff threads API wiring is a follow-up slice."
dl_state: "in_review"
next_step: "Wire a staff threads API endpoint (POST /threads/{thread_id}/relay) calling persona_relay with the real per-role allowlist, and back the audit hook with staff.audit.record_audit."
branch: "claude/3705-barb-relay-grok"
---
