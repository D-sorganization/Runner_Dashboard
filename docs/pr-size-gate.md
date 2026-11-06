# PR size advisory (#3963)

`scripts/pr_size_gate.py` computes one number for a branch: **changed lines** in
the PR slice, using `git diff --numstat` between the merge base and `HEAD`,
summing added + deleted across every changed file. That total includes generated
files and lockfiles.

The fleet **prefers small, cohesive PRs**. Line count is a **standard**, not a
hard gate: CI, pre-push hooks, and lane policy hooks **warn** when the count
exceeds the reference cap (default 500). They do **not** block push or merge.
Do **not** split a cohesive fix only to stay under a line count.

## Where the count runs

1. **Local pre-push hook.** Installed into each lane's worktree by
   `scripts/lane_policy_hooks.py`. It runs `pr_size_gate.py` before `git push`
   and logs a warning when over the reference cap.
2. **CI mirror.** The `pr-size-advisory` job in `.github/workflows/ci-standard.yml`
   runs on every `pull_request`. It checks out full history, then:

   - **PRs targeting `main`:** run
     `python scripts/pr_size_gate.py --merge-base origin/main --pr "$PR_NUMBER"`.
     The merge-base is recomputed against the runner's `origin/main` after
     fetch (same as the pre-push hook). Do **not** pass the frozen
     `pull_request.base.sha` here: that snapshot can lag behind `main` and would
     incorrectly count commits merged to `main` after the event.
   - **Stacked PRs** (base ref is not `main`): run with
     `--merge-base-ref "$PR_BASE_SHA"` where `PR_BASE_SHA` is
     `github.event.pull_request.base.sha`. The count is **incremental**: from
     `git merge-base(PR_BASE, HEAD)` to `HEAD`, not the cumulative diff from
     `main`.

   The job exits successfully in all cases; when over the cap it emits a
   workflow warning and applies the `size:large` label (best-effort).

Strict enforcement (exit 1 when over the cap and not allowlisted) is **opt-in**:
`python scripts/pr_size_gate.py --strict …`.

## Allowlist (`config/pr_size_allowlist.json`)

The file shape is unchanged:

```json
{ "allowed_prs": [] }
```

Dieter may list PR numbers there for bookkeeping or messaging. It does **not**
control merge — merge is gated by required checks only.

Agents must still not add their own PR numbers without owner approval, use
`--strict` to bypass advisory intent, or use `git push --no-verify` to skip
fleet hygiene hooks for unrelated reasons.

## Per-file line cap (ci-health-check)

The `Verify no source file exceeds 500 lines` step in `ci-health-check` is a
**soft** guideline: it prints `::notice` when a changed source file exceeds 500
lines and does not fail the job.
