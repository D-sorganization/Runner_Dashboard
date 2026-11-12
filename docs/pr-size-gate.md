# PR size advisory (#3963, fleet standard 2026-11-06)

Dieter standing order (2026-11-06): **small PRs are a coding standard, never a merge
gate.** A cohesive ~600-line fix ships as one PR. Line-count tooling **warns** and
may label; it **never** fails required checks or blocks push/merge.

`scripts/pr_size_gate.py` computes one number for a branch: **changed lines** in
the PR slice, using `git diff --numstat` between the merge base and `HEAD`,
summing added + deleted across every changed file. That total includes generated
files and lockfiles.

Do **not** split a cohesive fix only to stay under a line count.

## Where the count runs

1. **Local pre-push hook.** Installed into each lane's worktree by
   `scripts/lane_policy_hooks.py`. It runs `pr_size_gate.py` before `git push`
   and logs a warning when over the reference cap (exit 0).
2. **CI mirror.** The `pr-size-advisory` job in `.github/workflows/ci-standard.yml`
   runs on every `pull_request`. It checks out full history, then:

   - **PRs targeting `main`:** run
     `python scripts/pr_size_gate.py --merge-base origin/main --pr "$PR_NUMBER"`.
   - **Stacked PRs** (base ref is not `main`): run with
     `--merge-base-ref "$PR_BASE_SHA"` for an incremental count.

   The job **always exits successfully**; when over the cap it emits
   `::warning::`, `::notice::`, and applies the `size:large` label (best-effort).

## `--strict`

Deprecated. Retained for backward compatibility only; it does **not** exit non-zero.

## Allowlist (`config/pr_size_allowlist.json`)

Owner bookkeeping only; does not gate merge.

## Per-file line cap (ci-health-check)

The `Advisory per-file line count` step in `ci-health-check` prints `::notice`
when a changed source file exceeds 500 lines and does not fail the job.

## Outside this repository

RM **fleet context gate** failures citing `pr_size_exceeded` are enforced in
**Repository_Management** (not Runner_Dashboard); fleet alignment is tracked as
[Repository_Management#2822](https://github.com/D-sorganization/Repository_Management/issues/2822).
This repo mirrors advisory behavior only.
