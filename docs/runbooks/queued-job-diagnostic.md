# Queued-job diagnostic (read-only, #2897)

Use when many workflow runs stay **queued** while self-hosted runners look idle. This path is **read-only** — it does not cancel runs, relabel jobs, or change admission gates.

## When to run

- Org queue depth ≥ ~50 during an incident, or sustained `waiting_for_runner` / label mismatch symptoms.
- Before changing runner provisioning or merge-queue routing — confirm the stall class with a snapshot first.

## CLI (offline fixtures or exported JSON)

From the repo root:

```bash
python shared_scripts/queued_job_diagnostic.py \
  --queued-jobs /path/to/queued_jobs.json \
  --runners /path/to/runners.json
```

DbC: per-class counts must sum to the sampled job count; never treat HTTP **403** on runner-group reads as success.

## Hub API (live sample)

On a hub with GitHub admin integration configured:

```bash
curl -sS -H "Authorization: Bearer $DASHBOARD_API_KEY" \
  "http://127.0.0.1:8321/api/queue/diagnose" | jq '.wait_reason_classification, .workflow_label_policy'
```

Response fields:

- **`wait_reason_classification`** — shared classifier output (`label_mismatch`, `waiting_for_runner`, …).
- **`workflow_label_policy`** — static cross-check against `config/public_repo_heavy_jobs.json`.

See also `GET /api/runners/label-guidance` and `docs/runner-labels.md` for provisioning **`d-sorg-fleet-public-heavy`**.

## Findings publication

Full analysis: [`docs/research/queued-job-backlog-2897-findings.md`](../research/queued-job-backlog-2897-findings.md).

Publication copy for GitHub comments (Barb relay): [`docs/research/2897-publication-record.md`](../research/2897-publication-record.md).
