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
| `d-sorg-fleet-queue`   | Reserved slot | `merge_group` validation only, to keep merge-queue checks from waiting behind PR builds         | Pull-request builds, scheduled and manual jobs                |
| `d-sorg-fleet-browser` | Playwright/Vitest-capable | Frontend/E2E jobs in `frontend-tests.yml`, opt-in via the `CI_BROWSER_RUNNER_LABEL` repo variable (#3045) | The node-c runners (node-c-1/-2); never assign this label to them |

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

### Merge queue reserved slot

```yaml
runs-on: [self-hosted, Linux, X64, d-sorg-fleet-queue]
```

This label is reserved for `merge_group` jobs. Each host must advertise it on
one reserved runner slot before workflows route jobs to it. Do not assign it to
PR builds or maintenance jobs; the reservation is what protects merge checks
from the shared PR queue. Keep merge-queue cancellation disabled until the
owner records approval in the enabling PR.

## Opt-In Reserved-Runner Switch for merge_group (#3046, implements #2768)

`vars.CI_QUEUE_RUNNER_LABEL` is a repo variable, separate from the
`d-sorg-fleet-queue` taxonomy label above. When set (and
`CI_QUEUE_RUNNER_MODE` is not `hosted`), every required `merge_group` job on
`ci-standard.yml`, `frontend-tests.yml`, `ci-secrets.yml`,
`anti-phantom-merge.yml`, and `ci-auto-deploy-regression.yml` requests that
label instead of the shared `d-sorg-fleet` pool, so a merge group never waits
behind PR work. `pull_request`, `push`, `schedule`, and `workflow_dispatch`
jobs never request it. `CI_QUEUE_RUNNER_MODE=hosted` and `priority:expedite`
still take precedence, and fork PRs still never reach the fleet.

**Rollout:**

1. Org admin registers one or more self-hosted runners carrying the label you
   intend to put in `CI_QUEUE_RUNNER_LABEL` (for example `d-sorg-fleet-queue`)
   — TODO for the org admin; this PR does not register runners or edit
   organization runner labels.
2. Set the repo variable `CI_QUEUE_RUNNER_LABEL` to that label. Until it is
   set, every workflow in scope resolves exactly as it does today — the
   switch is a no-op by default.
3. Watch the next few merge-group runs: a job queues visibly on the reserved
   label. If no runner is online for that label, the job queues (and times
   out per its `timeout-minutes`) rather than silently falling back to the
   shared `d-sorg-fleet` pool.

**Rollback:** unset (or empty) the repo variable `CI_QUEUE_RUNNER_LABEL`.
No workflow file change is needed — every merge_group job immediately
resumes resolving to `d-sorg-fleet` on the next run.

### Browser runner (opt-in switch, #3045)

```yaml
runs-on: [self-hosted, Linux, X64, d-sorg-fleet-browser]
```

`frontend-tests.yml` does not use this snippet directly: every job's
`runs-on` already ends in `... || vars.CI_BROWSER_RUNNER_LABEL || 'd-sorg-fleet'`,
so setting the `CI_BROWSER_RUNNER_LABEL` repository variable to
`d-sorg-fleet-browser` is enough to opt every frontend/E2E job in at once. With
the variable unset, those jobs resolve exactly as they do today (bare
`d-sorg-fleet`).

**Rollout order:**

1. Org admin adds the `d-sorg-fleet-browser` label (Playwright system deps +
   Node, never node-c-1/-2) to the runners that should carry it.
2. Verify at least 8 of those runners are online and advertising the label.
3. Set the repository variable `CI_BROWSER_RUNNER_LABEL=d-sorg-fleet-browser`.
   Frontend/E2E jobs route to the new label on the next run; fork PRs and
   hosted-mode PRs are unaffected (RM#1989).
4. Restart node-c-1 and node-c-2 last, now that they no longer need to serve
   frontend/E2E jobs under the bare `d-sorg-fleet` label.

**Rollback:** unset `CI_BROWSER_RUNNER_LABEL` (frontend/E2E jobs fall back to
`d-sorg-fleet` immediately); stop node-c-1/-2 again first if they were
restarted into serving frontend/E2E traffic.

### Public-repo heavy-job group (#3084, epic #3077 slice 6)

`d-sorg-fleet-public-heavy` is the dedicated label for the three CI jobs
that must stay self-hosted forever, even once this repository's visibility
flips to public: Playwright e2e and Staff Console e2e (`frontend-tests.yml`)
and the auto-deploy regression suite (`ci-auto-deploy-regression.yml`). This
is the opposite of the generic `!github.event.repository.private &&
vars.CI_RUNNER_MODE != 'local'` switch that light jobs use to move to
`ubuntu-latest` once public — `docker-build.yml`'s `docker-image` job
deliberately keeps that generic switch instead (#2577: a dedicated
self-hosted docker pool would starve the deploy check), but these three jobs
have no such starvation concern, so they are carved out onto their own label
rather than following the generic switch. Each job also sets `environment:
ci-self-hosted-heavy-public` as an approval point. `config/public_repo_heavy_jobs.json`
is the single source of truth naming these jobs, the label and the
environment; `tests/test_public_repo_heavy_job_runner_split.py` checks every
entry against its workflow file.

**Owner step (not performed by this PR — no runner re-registration or
reconfiguration happens here):**

1. Create a self-hosted runner group restricted to this repository and to
   the `Frontend Tests` and `Node auto-deploy regression` workflows (GitHub
   organization settings → Actions → Runner groups), and register the
   `d-sorg-fleet-public-heavy` label on the runners assigned to it.
2. Optionally add required reviewers or a wait timer to the
   `ci-self-hosted-heavy-public` environment (repository settings →
   Environments) if an extra approval gate is wanted before a heavy job runs.

Until that owner step runs, `d-sorg-fleet-public-heavy` behaves like any
other as-yet-unregistered label: matching jobs queue and time out rather
than silently falling back to a different pool.

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
`lockfile-upgrade.yml`, `release.yml`.

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

Until node-a exposes distinct NVMe and HDD pools, all labels resolve to
the same physical runners. The taxonomy labels are registered but not yet
enforced by GitHub Actions runner groups. The audit is intentionally
conservative: it only fails on explicit misrouting, not on neutral-label usage.

See also: [`docs/runbooks/runner-routing-labels.md`](runbooks/runner-routing-labels.md)
for the operator runbook.
