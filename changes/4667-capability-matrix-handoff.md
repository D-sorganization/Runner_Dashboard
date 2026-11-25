---
issue: 4667
summary: "Document handoff_state separately from status and surface, including the required actor and Barb lease headers."
---

The inventory module docstring records that transition_handoff writes handoff_state and status only, so this change touches the path the issue names without changing routing or safety policy.
