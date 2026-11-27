---
issue: 4685
summary: "Point live operator docs at RM #2851, the configured message board, and GET /api/priorities"
paths: "docs/agents/messenger-agents.md, docs/agents/desk-cli-lane-claimability.md, docs/agents/grok.md, docs/runbooks/hub-sentinel.md, docs/runbooks/release-target-qualification.md, tests/unit/test_operator_docs_board_pointers.py"
---

Live routing now separates governing orders (RM #2851), rolling messages
(`config/agent_communication.json`), and the Friday Board agenda
(`GET /api/priorities`). Dated 2026-10-27 notes in messenger-agents.md still
name board #2376. `docs/coordination-api.md` is unchanged because open PRs
#4635 and #4636 already edit it.
