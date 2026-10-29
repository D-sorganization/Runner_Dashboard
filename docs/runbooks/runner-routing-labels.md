# Runner Routing Labels

Issue: `#757`

## Purpose

The current fleet still routes almost every workflow to the neutral
`d-sorg-fleet` label. That is safe for today's single-pool host, but it is not
specific enough for the planned dual-tier ControlTower design.

This runbook defines the label taxonomy that `Runner_Dashboard` should use once
the NVMe and HDD pools are live, and it documents the offline audit that keeps
workflow routing honest without requiring WSL to be online.

## Label Taxonomy

| Label                  | Use for                                                                                     | Avoid for                                                            |
| ---------------------- | ------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| `d-sorg-fleet-bulk`    | docs, governance, lightweight audits, queue hygiene, secret scans, release/tag verification | Docker builds, dependency-heavy test jobs, cache-heavy native builds |
| `d-sorg-fleet-fast-io` | dependency installs, large test suites, Playwright, lockfile refreshes, native-build churn  | pure docs/governance workflows                                       |
| `d-sorg-fleet-docker`  | Docker image builds, Trivy scans, container-layer churn                                     | lightweight maintenance workflows                                    |
| `d-sorg-fleet-nvme`    | explicit physical placement on the NVMe tier when the fastest pool is required              | lightweight workflows that can stay on HDD capacity                  |
| `d-sorg-fleet-queue`   | reserved runner capacity for `merge_group` validation only                                  | PR builds, schedules, and manual jobs                                |

## Workflow Classes

The policy file [`config/workflow_runner_routing_policy.json`](../../config/workflow_runner_routing_policy.json)
tracks the current routing plan.

- `bulk`: governance and read-mostly maintenance workflows that should live on
  the HDD tier once it exists.
- `fast-io`: dependency-heavy CI/test workflows that should prefer
  `d-sorg-fleet-fast-io` or `d-sorg-fleet-nvme`.
- `docker`: container-build workflows that should avoid the bulk label and
  prefer `d-sorg-fleet-docker` or `d-sorg-fleet-nvme`.

## Current Transition Rule

Until ControlTower exposes distinct NVMe and HDD pools, workflows may remain on
the neutral `d-sorg-fleet` label. The audit is intentionally conservative:

- it fails only when a workflow is explicitly misrouted to a forbidden tier;
- it reports recommendations when a workflow is still neutral and should be
  migrated later.

That keeps current CI stable while making the future label migration concrete.

## Merge Queue on GitHub-Hosted Runners (private repo)

`ci-standard.yml` stays on `d-sorg-fleet` while the repository is private unless
you opt in merge-queue runs to `ubuntu-latest`:

```bash
gh variable set CI_QUEUE_RUNNER_MODE --body hosted
```

Revert to the fleet default for merge-group checks:

```bash
gh variable delete CI_QUEUE_RUNNER_MODE
```

PR and push events are unchanged; only `merge_group` honors this variable.

## Merge Queue Reservation

`d-sorg-fleet-queue` is a dedicated routing label for merge-group checks. Add
it only to the reserved runner slot on each participating host, and use it only
for jobs running on the `merge_group` event. Keep it off PR builds and general
maintenance so queued merges retain reserved capacity. Do not enable live
superseded-run cancellation until the owner records approval in the enabling
PR; first validate the reaper in dry-run mode.

## Reserved Runner for merge_group (#3046, implements #2768)

`vars.CI_QUEUE_RUNNER_LABEL` routes required `merge_group` jobs on
`ci-standard.yml`, `frontend-tests.yml`, `ci-secrets.yml`,
`anti-phantom-merge.yml`, and `ci-auto-deploy-regression.yml` to a reserved
runner label instead of the shared `d-sorg-fleet` pool, so a merge group
never queues behind PR CI. It is separate from, and layered on top of,
`CI_QUEUE_RUNNER_MODE` (which routes `merge_group` to GitHub-hosted runners
instead): `CI_QUEUE_RUNNER_MODE=hosted` and `priority:expedite` both still
take precedence over the reserved-label branch.

**Rollout:**

1. Org admin registers a self-hosted runner carrying the chosen label (for
   example `d-sorg-fleet-queue`, the taxonomy label above) — a manual,
   human-only step; nothing in this change registers runners or edits
   organization runner labels.
2. Set the repo variable `CI_QUEUE_RUNNER_LABEL` to that label name. Unset,
   every in-scope workflow resolves exactly as it did before this change —
   verify this explicitly after any related edit.
3. Confirm the next merge-group run's `pick-runner`/equivalent job queues on
   the reserved label (check the job's `runs-on` in the Actions UI), then
   watch merge_group first-job wait time trend toward the ≤5 min p90 target.

**Rollback:** unset `CI_QUEUE_RUNNER_LABEL` on the repo. No workflow edit or
redeploy is required — the next merge_group run resolves straight back to
`d-sorg-fleet`. If a queue-labeled runner goes offline, jobs queue visibly
on that label rather than silently falling back to the shared pool; unsetting
the variable is the fastest way to restore shared-pool routing.

## Offline Audit

Run the audit from a checkout with:

```bash
python scripts/check_workflow_runner_routing.py
```

Useful flags:

```bash
python scripts/check_workflow_runner_routing.py --format json
python scripts/check_workflow_runner_routing.py --workflow-dir .github/workflows
python scripts/check_workflow_runner_routing.py --policy config/workflow_runner_routing_policy.json
```

Expected behavior:

- Docker/build/native dependency workflows pinned only to `d-sorg-fleet-bulk`
  are reported as violations.
- Lightweight maintenance workflows pinned to `d-sorg-fleet-fast-io`,
  `d-sorg-fleet-docker`, or `d-sorg-fleet-nvme` are reported as violations.
- Workflows still on neutral `d-sorg-fleet` show up as migration
  recommendations, not failures.

## Immediate Workflow Guidance

- `docker-build.yml` is the canonical `docker` workflow and must not move to
  `d-sorg-fleet-bulk`.
- `ci-standard.yml`, `ci-nightly.yml`, `frontend-tests.yml`, `release.yml`,
  and lockfile/repair workflows are the first `fast-io` candidates.
- `Agent-*`, `labels-sync.yml`, taxonomy workflows, spec checks, and verify-tag
  flows are the first `bulk` candidates.
