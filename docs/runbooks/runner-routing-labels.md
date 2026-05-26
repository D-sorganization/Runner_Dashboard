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
