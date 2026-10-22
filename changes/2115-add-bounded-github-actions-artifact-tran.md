---
issue: 2115
summary: "Add bounded GitHub Actions artifact transport."
dl_state: "in_review"
next_step: "Complete protected pre-PR qualification, then publish the local follow-up to PR #2125."
branch: "codex/2115-artifact-transport"
paths: "`backend/gh_client.py`, `tests/test_gh_actions_artifacts.py`"
---

Branch codex/2115-artifact-transport, PR #2125; local follow-up ae7e9ef1 is unpushed. The async artifact-client fixture closes its clients on the pytest-owned event loop; the synthetic BasicAuth URL has a narrow inline secret allowlist. Python 3.11 xdist regression is green; 69 focused tests and the non-slow Linux suite (7,255 passed, 46 skipped, 1 xfailed) pass. Ruff, format, targeted mypy and detect-secrets baseline comparison pass. Parent owns protected pre-PR qualification/publication. The unchanged RD base has inherited 5 WIP entries against a declared limit of 4; no strict-governance-green claim is made.
