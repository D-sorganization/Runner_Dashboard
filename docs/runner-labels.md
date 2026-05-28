# Runner Label Taxonomy

**Issue:** #757
**Status:** Active — transition to tiered labels in progress

This document describes the canonical runner label taxonomy used by the
D-sorganization fleet. Use these labels in `runs-on` to route workflow jobs
to the correct runner tier.

## Label Taxonomy

| Label                  | Storage tier | Use for                                                                                          | Avoid for                                                      |
| ---------------------- | ------------ | ------------------------------------------------------------------------------------------------ | -------------------------------------------------------------- |
| `d-sorg-fleet-nvme`    | NVMe SSD     | Compile-heavy native builds, large dependency installs, Playwright suites, cache-intensive jobs  | Lightweight governance, docs checks, read-mostly maintenance   |
| `d-sorg-fleet-fast-io` | NVMe or SSD  | CI test suites, lockfile refreshes, Jules Auto-Repair / PR-AutoFix, release builds               | Pure documentation or governance                               |
| `d-sorg-fleet-docker`  | NVMe or SSD  | Docker image builds, Trivy scans, container-layer cache churn                                    | Lightweight maintenance workflows                              |
| `d-sorg-fleet-bulk`    | HDD          | Governance, issue taxonomy sync, label sync, spec checks, secret scans, lease reaper, verify-tag | Docker builds, native-build CI, lockfile refreshes, Playwright |

## Neutral Labels (safe during transition)

These labels continue to work on any available runner and may be used until
the dual-tier pool is live:

- `d-sorg-fleet`
- `self-hosted`

## runs-on Snippets

Copy-paste these snippets into your workflow's `runs-on` field.

### NVMe tier (explicit physical placement)

```yaml
runs-on: [self-hosted, Linux, X64, d-sorg-fleet-nvme]
```

### Fast I/O (NVMe or SSD — recommended for most CI)

```yaml
runs-on: [self-hosted, Linux, X64, d-sorg-fleet-fast-io]
```

### Docker builds

```yaml
runs-on: [self-hosted, Linux, X64, d-sorg-fleet-docker]
```

### Bulk / HDD (governance and maintenance)

```yaml
runs-on: [self-hosted, Linux, X64, d-sorg-fleet-bulk]
```

## Workflow Classes

The routing policy is maintained in
[`config/workflow_runner_routing_policy.json`](../config/workflow_runner_routing_policy.json).
It organizes workflows into three classes:

### `bulk`

Lightweight maintenance, governance, and read-mostly workflows that should
stay on the HDD tier once the dual-pool host is live.

**Recommended:** `d-sorg-fleet-bulk`
**Forbidden:** `d-sorg-fleet-docker`, `d-sorg-fleet-fast-io`, `d-sorg-fleet-nvme`

Examples: `Agent-Fleet-Dashboard.yml`, `labels-sync.yml`, `ci-spec-check.yml`,
`Agent-Lease-Reaper.yml`, `verify-tag.yml`.

### `fast-io`

High-churn test/build workflows that should prefer a fast-I/O or NVMe label
once tiered pools are available.

**Recommended:** `d-sorg-fleet-fast-io`, `d-sorg-fleet-nvme`
**Forbidden:** `d-sorg-fleet-bulk`

Examples: `ci-standard.yml`, `ci-nightly.yml`, `frontend-tests.yml`,
`Jules-Auto-Repair.yml`, `Jules-PR-AutoFix.yml`, `release.yml`.

### `docker`

Container-build workflows that should avoid the HDD bulk tier.

**Recommended:** `d-sorg-fleet-docker`, `d-sorg-fleet-nvme`
**Forbidden:** `d-sorg-fleet-bulk`

Examples: `docker-build.yml`.

## Offline Audit

The routing audit runs without WSL being online. Run it from any checkout:

```bash
python scripts/check_workflow_runner_routing.py
```

Options:

```bash
# JSON output
python scripts/check_workflow_runner_routing.py --format json

# Custom workflow directory
python scripts/check_workflow_runner_routing.py --workflow-dir .github/workflows

# Custom policy file
python scripts/check_workflow_runner_routing.py --policy config/workflow_runner_routing_policy.json
```

The audit script reports:

- **violations** — workflows explicitly misrouted to a forbidden tier (fails CI).
- **recommendations** — workflows still on neutral `d-sorg-fleet` that should
  migrate (informational only, does not fail CI).

The same findings are available via the dashboard API:

```
GET /api/runners/label-audit
GET /api/runners/label-guidance
```

## Audit Rules

1. A workflow explicitly pinned to a **forbidden** label is a **violation**.
2. A workflow using an **unknown** tier label (starts with `d-sorg-fleet-` but
   not in the taxonomy) is a **violation**.
3. A workflow using only **neutral** labels is a **recommendation** — migration
   will be needed before the dual-tier pool goes live.
4. A workflow using at least one **recommended** label passes the audit.
5. No hosted-runner fallback is introduced by this taxonomy.

## Current Status

Until ControlTower exposes distinct NVMe and HDD pools, all labels resolve to
the same physical runners. The taxonomy labels are registered but not yet
enforced by GitHub Actions runner groups. The audit is intentionally
conservative: it only fails on explicit misrouting, not on neutral-label usage.

See also: [`docs/runbooks/runner-routing-labels.md`](runbooks/runner-routing-labels.md)
for the operator runbook.
