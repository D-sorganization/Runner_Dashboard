---
issue: 4667
summary: "Document handoff_state separately from status and surface, including the required actor and Barb lease headers."
---

The inventory module docstring records that transition_handoff preserves surface and updates handoff state, status, owner, fencing epoch, handoff metadata, and updated_at. This change touches the path the issue names without changing routing or safety policy.
