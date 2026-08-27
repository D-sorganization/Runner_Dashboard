# Current Handoff — Release 4.9.34 Qualification & Release Recovery

Last updated: 2026-08-27T06:00:00-07:00

## Identity

- Repository: `D-sorganization/Runner_Dashboard`
- Working directory:
  `C:\Users\diete\Repositories\Runner_Dashboard-worktrees\1129-release-recovery`
- Branch: `fix/issue-1129-release-recovery`
- Baseline commit: `d01cbfa`
- Implementation commit: `SELF` — resolve with `git rev-parse HEAD`
- Pull request: [#1130](https://github.com/D-sorganization/Runner_Dashboard/pull/1130)
- Governing issue: [#1129](https://github.com/D-sorganization/Runner_Dashboard/issues/1129)

## Current Objective

1. Provide fail-closed recovery for releases where artifact generation and tag creation succeeded but release publication failed (#1129).
2. Align canonical capacity policy with interactive-safe operations and qualified 4.9.34 release.

## Implemented

- The release workflow now supports explicit recovery of an existing tag. It
  checks out the tag source, verifies exact commit identity, annotated type,
  and governed release header, then skips tag creation and push.
- GitHub release publication now uses an explicit repository identity instead
  of inferring it through the self-hosted runner filesystem.
- Static workflow contracts cover recovery input, exact-source verification,
  mutation guards, prior-tag selection, and explicit release targeting.
- `config/runner-schedule.json` defaults to one weekday-day runner, two
  weekend-day runners, and two overnight runners, with `max_count: 2`.
- `tests/test_config_schema.py` enforces the exact canonical windows and counts.
- `docs/deployment-model.md` matches the one-normal/two-maximum contract.

## Validation

- Four release-recovery workflow contracts tested in `tests/test_release_workflow_yaml.py`.
- 139 focused release and workflow-governance tests pass serially.
- Scoped pre-commit passes Ruff lint/format, Prettier, gitleaks, and detect-secrets.
- `git diff --check` passes.

## Next Steps

1. Merge `origin/main` into `fix/issue-1129-release-recovery`, run tests, and enable auto-merge.
2. After protected merge, late-stage release recovery can be dispatched using `recover_existing_tag=true`.
