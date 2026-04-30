# Runbook: CI Failure Triage

The CI Standard workflow (`.github/workflows/ci-standard.yml`) is failing on
`main` or on a critical PR. This blocks merges and (if main is red) the next
deploy.

## Symptom

- The CI Standard check is red on `main` after a merge.
- Multiple PRs are failing the same job (`quality-gate`, `security-scan`,
  or `tests`).
- Spec Check (`ci-spec-check.yml`) flags backend changes without a
  corresponding `SPEC.md` update.
- Jules PR AutoFix is looping on a PR without making progress.

## Severity

**P1** if `main` is red and blocks the deploy pipeline.
**P2** if a single PR is red but `main` is green.
**P3** if only the non-blocking pip-audit warnings are firing.

## Detection

- The PR or commit page on GitHub shows a red X on "CI Standard".
- `gh pr checks <pr-number>` lists failures.
- The Workflows tab in the dashboard surfaces consecutive failures.
- Slack/Discord agent channel messages from Jules Control Tower reporting
  remediation in flight.

## Diagnosis

```bash
# 1. List recent CI Standard runs and statuses.
gh run list --workflow ci-standard.yml --limit 10

# 2. Inspect the failing run (replace <run-id>).
gh run view <run-id> --log-failed | head -200

# 3. Reproduce the failing job locally — match the CI commands exactly:
ruff check backend/
ruff format --check backend/
mypy backend/ --ignore-missing-imports --exclude 'backend/__pycache__' --no-implicit-optional
bandit -r backend/ -ll -ii --exclude backend/__pycache__
pytest tests/ -q --tb=short

# 4. Spec-check failures: see which backend files changed without a SPEC.md
#    bump on this branch.
git diff --name-only origin/main...HEAD -- backend/ SPEC.md

# 5. Confirm the failure is not a flake by re-running the failed jobs only.
gh run rerun <run-id> --failed
```

## Mitigation

```bash
# A. For lint/format failures, auto-fix and push.
ruff check backend/ --fix
ruff format backend/
git add -u && git commit -m "chore: ruff autofix" && git push

# B. For spec-check failures on a legitimate exemption, apply the label.
gh pr edit <pr-number> --add-label spec-exempt

# C. For a flaky test, re-run only failed jobs.
gh run rerun <run-id> --failed

# D. If main is red and a quick fix is not possible, revert the offending
#    merge to unblock the deploy pipeline.
git revert <bad-merge-sha>
git push origin main
```

If Jules PR AutoFix has been looping for >30 min without progress, comment
on the PR to disengage it (`/jules pause` or remove the `claim:jules`
label) and take it over manually.

## Resolution

- Land the real fix in a follow-up PR. If the failure was a flaky test,
  add deterministic seeding and an `xfail` only as a last resort with an
  explicit tracking issue.
- Update `tests/api/` or `tests/frontend/` with the regression case so the
  same failure cannot recur silently.
- For repeated security-scan findings, upgrade the offending dependency in
  `backend/requirements.txt` and pin to a known-good version.
- For spec-check noise, document the exemption pattern in
  `docs/issue-taxonomy.md` so future PRs know when `spec-exempt` is correct.
- Update this runbook if a new CI job is added that operators commonly
  encounter.

## Postmortem Template

[`postmortem-template.md`](./postmortem-template.md)
