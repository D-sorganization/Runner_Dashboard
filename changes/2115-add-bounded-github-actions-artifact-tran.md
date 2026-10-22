---
issue: 2115
summary: "Add bounded GitHub Actions artifact transport."
dl_state: "in_review"
next_step: "Verify the qualified follow-up through required PR and merge-group CI; qualify consumer integration separately."
branch: "codex/2115-artifact-transport"
paths: "`backend/gh_client.py`, `tests/test_gh_actions_artifacts.py`"
---

Branch codex/2115-artifact-transport, PR #2125; async fixture correction ae7e9ef1 is qualified locally. The async artifact-client fixture closes its clients on the pytest-owned event loop; the synthetic BasicAuth URL has a narrow inline secret allowlist. Python 3.11 xdist regression is green; 69 focused tests and the non-slow Linux suite (7,255 passed, 46 skipped, 1 xfailed) pass. Ruff, format, targeted mypy and detect-secrets baseline comparison pass. Parent pre-PR qualification passed on corrected code; source publication and required CI remain distinct. The unchanged RD base has inherited 5 WIP entries against a declared limit of 4; no strict-governance-green claim is made.
