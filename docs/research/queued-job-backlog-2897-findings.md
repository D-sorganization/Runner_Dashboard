# Queued-job backlog findings (#2897)

Read-only research explaining why self-hosted runners sat **30–54% idle** on **2025-10-25** while roughly **400** GitHub Actions runs stayed queued. This document ties together workflow routing, runner labels, live snapshots already posted on [#2897](https://github.com/D-sorganization/Runner_Dashboard/issues/2897), and the dashboard diagnostic surfaces added in this change set.

**Verified against `main`:** run `git rev-parse HEAD` when reviewing; classifier logic lives in `shared_scripts/queued_job_diagnostic.py`.

## Executive summary

| Question | Answer |
| --- | --- |
| Dominant stall class (Oct 2025 backlog) | **`label_mismatch`** — jobs requested labels no online runner carried |
| Primary missing label | **`d-sorg-fleet-public-heavy`** (public-repo heavy CI jobs, #3084) |
| Idle runners with standard fleet labels | Yes — **`d-sorg-fleet`** pool was online and idle while heavy jobs waited |
| Does F5 (#2768, reserved `merge_group` label) help? | **No** — merge-group work used `d-sorg-fleet` / `CI_QUEUE_RUNNER_LABEL` and was not the starving queue |
| Policy change in the diagnostic PR? | **No** — instrumentation and docs only |

## Workflow and label sources (static)

Heavy jobs that must stay self-hosted after the repo goes public are listed in `config/public_repo_heavy_jobs.json`:

| Workflow | Job id | Required check name (approx.) | `runs-on` tail label |
| --- | --- | --- | --- |
| `frontend-tests.yml` | `playwright-e2e` | Playwright E2E smoke tests | `d-sorg-fleet-public-heavy` |
| `frontend-tests.yml` | `staff-console-e2e` | Staff Console e2e (fake providers) | `d-sorg-fleet-public-heavy` |
| `ci-auto-deploy-regression.yml` | `auto-deploy-regression` | Auto-deploy live regression | `d-sorg-fleet-public-heavy` |

Guardrails are enforced by `tests/test_public_repo_heavy_job_runner_split.py` (no silent flip to `ubuntu-latest` when public). Owner provisioning steps are documented in `docs/runner-labels.md` (scoped runner group + `ci-self-hosted-heavy-public` environment).

Merge-queue and bulk CI continue to target the shared **`d-sorg-fleet`** label family (`CI_QUEUE_RUNNER_LABEL` in workflows). That pool had spare capacity during the incident; the backlog was not merge-group starvation.

## Classifier behavior (`WaitReason`)

The shared script classifies each queued job sample:

1. `needs_blocked` / `concurrency_blocked` when metadata flags are present
2. `github_hosted` for hosted queues (`ubuntu-latest`, etc.)
3. `runner_group_access` when org runner-group visibility blocks the repo
4. **`label_mismatch`** when no **online** runner’s label set is a superset of the job’s `runs-on` labels
5. **`waiting_for_runner`** when labels match online runners but none are idle (capacity / pickup lag)

Bare fleet capability labels (for example `d-sorg-fleet-public-heavy` without the literal string `self-hosted`) are treated as **self-hosted fleet** jobs, not GitHub-hosted (fix merged via #3590).

DbC postconditions: per-class counts must sum to the sampled job count; API **403** responses must appear in `api_findings`, never be swallowed.

## Live snapshots (already on the issue)

Two dated snapshots on #2897 (2026-10-27 and 2026-10-30 PT) sampled Runner_Dashboard during large backlogs:

- **Snapshot 2 (203 jobs):** **100% `label_mismatch`** after the #3590 classifier fix — all waiting on **`d-sorg-fleet-public-heavy`** while **0** online runners carried that label and **21/21** online fleet runners were idle on other labels.
- **F5 recommendation:** `proceed: false` — reserved merge-group capacity does not address a missing **`public-heavy`** provisioning lane.

Partial runs also recorded **HTTP 403** on org runner-group reads for some integrations; those are findings, not grounds to bypass permissions.

## Read-only diagnostics in this repo

| Surface | Purpose |
| --- | --- |
| `python shared_scripts/queued_job_diagnostic.py --queued-jobs … --runners …` | Offline snapshot analysis (fixtures or owner-exported JSON) |
| `GET /api/queue/diagnose` | Live sample of queued jobs + runner inventory; now includes **`wait_reason_classification`** (shared classifier) and **`workflow_label_policy`** (static public-heavy cross-check) |
| `GET /api/runners/label-guidance` | Human-oriented label docs |
| `GET /api/runners/groups/{label}` | Count runners registered with a label |

Use `/api/queue/diagnose` during the next ≥50 queued-run event to refresh counts without mutating queue state.

**Publication (GitHub comments):** paste bodies from [`2897-publication-record.md`](2897-publication-record.md); operator runbook [`../runbooks/queued-job-diagnostic.md`](../runbooks/queued-job-diagnostic.md).

## Recommended follow-ups (policy — separate issues)

1. **Provision** self-hosted runners (or a dedicated runner group) with label **`d-sorg-fleet-public-heavy`**, scoped per `docs/runner-labels.md` / `ci-self-hosted-heavy-public`.
2. **Grant read** on org runner-groups to the dashboard integration where policy review requires group visibility (403 is expected today for some tokens).
3. **F5 (#2768):** remain **deferred** until a snapshot shows merge-group jobs stuck in `waiting_for_runner` with idle `d-sorg-fleet-merge-group` capacity — not observed in Oct 2025 data.

## Non-claims

This research does **not** cancel runs, relabel workflows, change admission gates, or prove org-wide runner-group absence when API reads return 403.
