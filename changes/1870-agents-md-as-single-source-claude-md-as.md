---
issue: 1870
summary: "AGENTS.md as single source; CLAUDE.md as @AGENTS.md stub; long form in repository-guide.md"
dl_state: "in_review"
next_step: "Merge into main via merge queue."
branch: "docs/1870-agents-md-single-source"
---

At the top of AGENTS.md, added repo quick reference and rules. Relocated long-form repository guidance (architecture tree, coding conventions, engineering principles, issue taxonomy, dev commands, system architecture) into `docs/agents/repository-guide.md`. Reduced CLAUDE.md to the `@AGENTS.md` import stub. AGENTS.md + CLAUDE.md combined size reduced from ~120KB to ~17.2KB (4,304 tokens, well under the 6k token budget). Added unit test suite in `tests/test_context_diet.py`.
