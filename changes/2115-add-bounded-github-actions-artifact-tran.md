---
issue: 2115
summary: "Add bounded GitHub Actions artifact transport."
dl_state: "in_review"
next_step: "Complete protected pre-PR qualification and review the transport contract before publication."
branch: "codex/2115-artifact-transport"
paths: "`backend/gh_client.py`, `tests/test_gh_actions_artifacts.py`"
---

Branch codex/2115-artifact-transport; implementation commits 48798bda and 72cef9e2. Added fail-closed bounded artifact pagination that preserves query filters and rejects external API origins, plus ZIP streaming with typed failures and credential-safe HTTPS redirects. Focused artifact, gh_client and retry suites: 68 passed; Ruff, format and targeted mypy passed. PR not opened. Protected central development-log qualification remains parent-owned; the unchanged RD base has inherited 5 WIP entries against a declared limit of 4, so this work does not claim strict governance green.
