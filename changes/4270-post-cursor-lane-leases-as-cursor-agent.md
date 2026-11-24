---
issue: 4270
summary: "Post cursor lane claims and presence as roster id cursor-agent."
---

Lane principal `agent-cursor` is authorized as `cursor`, which is not in `AGENT_IDS`. Lease and presence writes now send `cursor-agent` when the lane id is absent from the roster, and leave ids that are already roster entries unchanged.
