---
issue: 4574
summary: "Board rollover notice uses valid agent-message v1 JSON envelope for retired-board comment"
---

`execute_board_rollover` posts `_rollover_notice_envelope` as a fenced `<!-- agent-message v1 -->` JSON `message` on the retired coordination board so `agent_messages.py` replay stays valid (replaces plain-text rollover tag).
