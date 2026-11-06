# PR size gate (#3963)

Every pull request against this repository is capped at 500 changed lines,
counted the same way everywhere it is enforced: `git diff --numstat` between
the diff's merge-base with `origin/main` and `HEAD`, summing added + deleted
lines across every changed file. That total includes generated files and
lockfiles — there is no "hand-written lines only" carve-out in the
enforcement itself (lanes may still track hand-written line counts
informally when deciding how to slice work, but the gate counts everything).

## What counts the number

`scripts/pr_size_gate.py` is the single source of truth for the count. Both
enforcement points below call the exact same script, so they always agree:

1. **Local pre-push hook.** Installed into each lane's worktree by
   `scripts/lane_policy_hooks.py` during bootstrap. It runs
   `pr_size_gate.py` before `git push` and refuses the push if the diff is
   over the cap and not allowlisted.
2. **CI mirror.** The `pr-size-gate` job in `.github/workflows/ci-standard.yml`
   runs on every `pull_request` event. It checks out full history, then runs:

   ```
   python scripts/pr_size_gate.py --merge-base origin/main \
     --merge-base-ref "$PR_BASE_SHA" --pr "$PR_NUMBER"
   ```

   where `PR_BASE_SHA` is `github.event.pull_request.base.sha` (the tip of the
   PR's base branch at event time). For PRs targeting `main`, the effective
   range is the same triple-dot diff as the local hook; for stacked PRs
   (base branch not `main`), the count is **incremental**: from
   `git merge-base(PR_BASE, HEAD)` to `HEAD`, not the cumulative diff from
   `main`. If the local hook was ever skipped, bypassed, or simply not
   installed in a given worktree, this job still catches an oversize **slice**
   before merge.

The implementation is still one script; for default `main`-targeting branches,
the CI number matches the local hook. Stacked PRs use `--merge-base-ref` in CI
only so each layer is gated on its own delta.

## The allowlist is not a flag an agent can pass itself

`scripts/pr_size_gate.py` accepts a `--pr` number and an `--allowlist` path
(default `config/pr_size_allowlist.json`) so a specific, already-reviewed PR
can be let through despite being over the cap. The allowlist file is:

```json
{ "allowed_prs": [] }
```

a flat JSON list of PR numbers under `allowed_prs`. Agents must never:

- add their own PR number to this file,
- pass `--limit` with an inflated value to `pr_size_gate.py`,
- bypass the local hook with `git push --no-verify`,
- bypass the CI job with `SKIP=` or by disabling the step.

The allowlist is edited only by Dieter, by hand, after he has decided a
specific oversize PR is worth the exception (for example, a deletion-only
change or an already-reviewed large migration). If a change is genuinely
over the cap, the correct move is to split it into a first slice under the
cap and file a follow-up issue for the rest — exactly what `pr_size_gate.py`
prints when it fails:

```
size gate: NNN changed lines exceeds the 500-line cap; split into a first
slice and file a follow-up issue for the rest
```
