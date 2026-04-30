# Branch Protection — `main`

This document records the branch-protection contract for the `main` branch
of `runner-dashboard`. It is the policy companion to
[`.github/CODEOWNERS`](../.github/CODEOWNERS) and satisfies acceptance
criterion #2 of issue [#399](https://github.com/D-sorganization/Runner_Dashboard/issues/399).

## Required status checks (all must pass before merge)

The following checks must complete successfully on every pull request
targeting `main`:

- `quality-gate` — ruff lint, ruff format, mypy, no-placeholders, CODEOWNERS
  validity assertion, pip-audit (from `ci-standard.yml`).
- `security-scan` — pip-audit on `requirements.txt` (from `ci-standard.yml`).
- `tests` — pytest suite (from `ci-standard.yml`).
- `Spec Check` — SPEC.md updated when backend source files change
  (from `ci-spec-check.yml`); bypassable only via the `spec-exempt` label.

"Require branches to be up to date before merging" must be enabled so the
required checks always run against the merge target.

## Required reviewers (CODEOWNERS enforcement)

"Require a pull request before merging" with **"Require review from Code
Owners"** must be enabled. This makes the entries in `.github/CODEOWNERS`
mandatory reviewers for any PR that touches the matching paths:

- `/backend/security.py` — `@D-sorganization/security-reviewers`
- `/backend/auth_webauthn.py` — `@D-sorganization/security-reviewers`
- `/backend/dispatch_contract.py` — `@D-sorganization/security-reviewers`
  and `@D-sorganization/maintainers`
- `/backend/middleware.py` — `@D-sorganization/security-reviewers`
- `/backend/identity.py` — `@D-sorganization/security-reviewers`
- `/deploy/` — `@D-sorganization/maintainers`
- `/.github/workflows/` — `@D-sorganization/maintainers`
- `/docs/adr/` — `@D-sorganization/maintainers`
- `/SPEC.md` — `@D-sorganization/maintainers`

At least **one** approving review is required, and stale approvals must be
dismissed when new commits are pushed.

## History and merge hygiene

- **Linear history required** — merge commits are disallowed; PRs must be
  merged via squash or rebase.
- **No force pushes to `main`** — `Allow force pushes` must be disabled for
  everyone, including administrators.
- **No deletions** — `Allow deletions` must be disabled.
- **Auto-delete head branches after merge** must be enabled at the
  repository level so feature branches do not accumulate.
- **Conversation resolution required** — all review threads must be resolved
  before merge.

## Administrators

`Do not allow bypassing the above settings` should be enabled so that
administrators are bound by the same protections; emergencies are handled
via temporary policy lift, not silent bypass.

## CI enforcement of CODEOWNERS validity

The `quality-gate` job in `.github/workflows/ci-standard.yml` runs a
`CODEOWNERS validity` step that fails the build if `.github/CODEOWNERS` is
empty or contains no GitHub team/handle references matching
`@[A-Za-z0-9-]+(/[A-Za-z0-9-]+)?`. This satisfies acceptance criterion #3
of issue #399 and prevents accidental clobbering of the file.
