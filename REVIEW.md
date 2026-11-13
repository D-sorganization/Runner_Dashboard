# Review Rules: Runner_Dashboard

Repo-specific rules for anyone reviewing a Runner_Dashboard pull request. The
author checks them first with the `fleet-review` skill. A breach is a finding
only when it's cited here or in `AGENTS.md`, by file and section.

## Scope and Size

- Under 500 changed lines after formatters, targeting `main`, never stacked.
- Runner_Dashboard is the operator console. Maxwell pipeline code belongs in
  Maxwell_Daemon and fleet CI workflows in Repository_Management. A runtime
  import from a sibling repo is a finding: cross-repo traffic is HTTP only.

## Evidence

- New behaviour needs a test that fails on `main` (TDD): backend routes in
  `tests/api/`, frontend behaviour in `tests/frontend/`. A feature without a
  test is a finding, not a follow-up.
- A deploy or auto-release fix needs a failing regression test in the deploy
  smoke suite (`tests/deploy/`, `tests/test_main_ci_release_wait.py`) first.
- `python -m scripts.pre_pr` passes. The PR body says so, and names any gate
  skipped for an environmental reason.
- Loosening a tolerance, budget or coverage floor needs a
  `Tolerance-Change-Evidence: #N — <numbers>` commit trailer.

## Design

- Every `POST` route and dispatch envelope has pre/postconditions as pydantic
  models or `assert` blocks, following `backend/dispatch_contract.py`.
- Handlers take flat, typed payloads (Law of Demeter). Payload models are
  defined once and reused.
- Tabs fail independently: one `/api/*` 5xx must not break another tab.
- Python uses type annotations and `logging`, never `print()`. Frontend code
  is React + TSX under `frontend/src/` with design tokens, never plain JS.
- Schema changes are two-step: an additive ship, then removal in the next
  release.

## Generated and Shared Files

- Handoffs go in a change fragment under `changes/`
  (`scripts/changes_fragment.py`). Direct edits to `SPEC.md`,
  `docs/development/DEVELOPMENT_LOG.md` or `docs/development/HANDOFF.md`
  are findings.
- Backend changes need a `SPEC.md` change row from the fragment, or the
  `spec-exempt` label (`.github/workflows/ci-spec-check.yml`).

## Merging

- A PR that deletes files says `deletions-acknowledged: yes` on its own line
  in the body. Otherwise the arm guard refuses it.
- PRs are armed only with `scripts/automerge_guard.py`, never with a
  merge-method flag, `--admin` or a ruleset change.
- Commits use a GitHub noreply email.
- `Closes #N` sits on its own line. An issue closes only through a merged PR
  or an exempt label.
- No secrets, tokens or personal data in code, tests or the PR body.
