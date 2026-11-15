# CI Hosted Fallback (When the Self-Hosted Fleet Is Down)

Use this runbook when pull request or merge-queue checks of this repository
wait for a self-hosted runner that is not coming. It lists the switches that
move CI to GitHub-hosted runners, and the one job that no switch moves
(#4268 H-06).

`tests/test_ci_hosted_fallback_doc.py` compares this page with
`.github/workflows/ci-standard.yml`. If you change the runner selection in the
workflow, that test fails until this page is updated.

## Where Jobs Run by Default

The `pick-runner` job of `ci-standard.yml` chooses the runner for most jobs.
It runs on `ubuntu-latest` itself, so the choice never waits for the fleet.

- This repository is public. With no switch set, its jobs run on
  `ubuntu-latest`.
- A private repository, or `CI_RUNNER_MODE=local`, sends them to the
  `d-sorg-fleet` pool.
- A pull request from a fork always runs on `ubuntu-latest` (RM#1989).

## Switches

`pick-runner` applies the first rule that matches, in this order.

| Order | Switch                                        | Set by                 | Effect                                                                                           |
| ----- | --------------------------------------------- | ---------------------- | ------------------------------------------------------------------------------------------------ |
| 1     | Fork pull request                             | automatic              | Hosted. Nothing overrides it.                                                                    |
| 2     | `force_local` input of `workflow_dispatch`    | whoever starts the run | Fleet, for that manual run only.                                                                 |
| 3     | `priority:expedite` label on the pull request | a maintainer           | Hosted, for that pull request and its merge group. See [escalation-lane.md](escalation-lane.md). |
| 4     | `CI_QUEUE_RUNNER_MODE=hosted`                 | repository variable    | Hosted, for `merge_group` runs.                                                                  |
| 5     | `CI_QUEUE_RUNNER_LABEL=<label>`               | repository variable    | `merge_group` runs go to that reserved fleet label. See [runner-labels.md](../runner-labels.md). |
| 6     | `CI_PR_RUNNER_MODE=hosted`                    | repository variable    | Hosted, for `pull_request` runs.                                                                 |
| 7     | `CI_RUNNER_MODE=local`                        | repository variable    | Fleet, for everything not matched above.                                                         |

`RD_FAST_CHECKS_RUNNER` (`hosted`, `local` or unset) is separate. It only
chooses the runner of the short aggregate jobs, such as `quality-gate`, in a
private repository's merge queue. Any other value fails `pick-runner`.

Repository variables are set under Settings, Secrets and variables, Actions,
Variables. A change applies to the next run; no workflow file changes. To go
back, restore the old value.

## Jobs That Stay on the Fleet

- `pr-guard` (shown as "PR freshness guard"). It calls
  `reusable-pr-freshness-guard.yml`, whose only job has the fixed labels
  `[self-hosted, d-sorg-fleet]`. It runs for every pull request from a branch
  of this repository, and no switch in the table changes it.

Lint, tests, the security scan and `quality-gate` all list `pr-guard` in
`needs`. So they do not start until it finishes, whatever runner they would
use themselves.

What this means in an outage:

- **Pull requests from this repository** show `pr-guard` as queued and nothing
  after it starts. The required `quality-gate` check does not post. Setting
  `CI_PR_RUNNER_MODE=hosted` or adding `priority:expedite` does **not** help.
- **Fork pull requests** are not affected: `pr-guard` is skipped for them.
- **Merge groups** are not affected by `pr-guard`: it runs only on
  `pull_request`. With the default routing of this public repository, or with
  `CI_QUEUE_RUNNER_MODE=hosted`, the merge queue needs no fleet runner in this
  workflow. A pull request that already has its required checks can still
  merge.

## What to Do

1. Confirm the cause. Open the run: if "PR freshness guard" is queued and
   `pick-runner` is green, the fleet has no free `d-sorg-fleet` runner.
   [ci-failure-triage.md](ci-failure-triage.md) covers the other causes.
2. Bring one `d-sorg-fleet` runner back. The guard takes under three minutes,
   so one runner clears the queue of waiting pull requests.
3. Do not cancel or re-run other pull requests' runs to free a runner, and do
   not cancel `merge_group` runs.
4. If no runner can come back soon, there is no hosted route for same-repo
   pull requests today. Tell the owner; the choice below is theirs.

## Not Done Here: a Hosted Route for `pr-guard`

Closing the gap needs a change to the runner labels of `pr-guard`: for
example, make the reusable workflow take the runner from `pick-runner`, as the
other jobs do. Issue #4268 requires the owner's approval for any runner-label
change, so this runbook only records the gap. The guard reads pull request
metadata and checks out two small folders; it has no need of fleet hardware.
