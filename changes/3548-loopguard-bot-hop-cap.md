---
issue: 3548
summary: 'Persist bot principals with `author_kind="bot"` so bot-to-bot turns do not reset `LoopGuard`, and enforce a configurable fail-closed handoff hop cap (`STAFF_HANDOFF_HOP_CAP`, default 3) across conversation handoffs (#3548).'
---

Persist bot principals with `author_kind="bot"` so bot-to-bot turns do not reset `LoopGuard`, and enforce a configurable fail-closed handoff hop cap (`STAFF_HANDOFF_HOP_CAP`, default 3) across conversation handoffs (#3548).
