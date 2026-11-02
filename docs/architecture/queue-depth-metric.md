# Queue depth metric definition (Paperclip panel #3874 / issue #3876)

**Status:** Proposed — implementation tracked in #3880.  
**Authors:** RD Paperclip vs RD expert panel (2026-11-02), filed after panel consensus 4-0.

## Problem

Runner Dashboard overload and queue-batching logic need a **single, explicit queue-depth signal** for backpressure. Today the primary overload path does **not** measure RD’s dispatch or job-board queue.

### Current implementation

`backend/coordination/overload.py::_fetch_queue_depth` paginates the GitHub REST API:

```http
GET /repos/{owner}/{repo}/actions/runs?event=merge_group&status=queued
```

It counts **queued GitHub Actions workflow runs** for the `merge_group` event — i.e. merge-queue validation runs waiting for runners — not:

- open agent dispatch slots,
- coordination job-board rows,
- org-wide `/api/queue` workflow backlog, or
- merge-queue *entries* (PRs waiting to enter the queue).

That count is useful for **CI merge-queue saturation** but is a poor proxy for **RD agent/dispatch pressure**. The panel (Fable/Barb) reclassified W1b from a one-line fix to this design step because the metric measures a **different system’s queue** and calls it RD depth.

### Current consumers

| Consumer | Field / usage | Notes |
|----------|----------------|-------|
| `coordination/overload.py` | `merge_queue_depth`, `queue_batching` | Drives DRAIN suggestions and batching hints |
| `coordination/queue_batching.py` | `queue_depth` argument | Compares depth vs `max_merge` ruleset parameter |
| `coordination/brief.py` | merge queue queued/in-progress (separate API) | Operator briefing text |
| `dispatch_queue_delay.py` | `QueueDepthSnapshot` (`queued_runs`, `queued_jobs`, `queue_p95_seconds`) | Agent dispatch throttling (#2034); **already models two axes** |
| `routers/queue.py` | org workflow run sample | Operator queue UI — different freshness contract |

Implementations must not conflate these without renaming fields (breaking change — out of scope for the first implementation PR).

## Design goals

1. **Semantic clarity** — field names state what is counted (GitHub merge runs vs RD jobs vs org runs).
2. **Observable on the hub** — no dependency on Postgres or new daemons; prefer existing GitHub + RD SQLite/HTTP sources.
3. **Stable for backpressure** — suitable for DRAIN / dispatch delay decisions (bounded API cost, documented staleness).
4. **Per-repo primary, org rollup optional** — overload today is per-repo; org-wide rollups remain briefing-only unless explicitly extended.

## Metric candidates (evaluated)

| ID | Definition | Source | Pros | Cons |
|----|------------|--------|------|------|
| **M0 (legacy)** | Count of `merge_group` Actions runs with `status=queued` | GitHub REST (current) | Already implemented; matches merge-queue CI pressure | Not RD dispatch depth; run fan-out skews under matrix |
| **M1** | Merge-queue **entries** waiting (PRs in queue) | GitHub GraphQL merge queue / ruleset APIs | Closer to “jobs waiting to merge” | Rate limits; not identical to queued *runs* |
| **M2** | Org/org-repo **workflow runs** queued + in_progress (excl. policy filters) | `/api/queue` aggregation / `gh` list runs | Matches operator “queue stuck” runbook | Heavy; mixes unrelated workflows |
| **M3** | **RD dispatch backlog** — count of active+queued staff/agent runs from coordination board | `coordination/staff_view` / job board | Aligns with agent pressure | Not repo-scoped today; needs definition of “counts toward depth” |
| **M4** | **Claimed-but-not-finished jobs** on job board | SQLite job board store | True RD job queue | Requires schema contract; depends on lease/claim epic (#3877+) |

## Recommended canonical metrics (v1)

Adopt **two labeled metrics** instead of overloading one integer:

### 1. `github_merge_queue_run_depth` (retain M0, rename in API)

- **Definition:** Number of GitHub Actions workflow runs with `event=merge_group` and `status=queued` for `{owner}/{repo}`, capped at `_MAX_QUEUE_RUNS_SCANNED` (300) with a warning when truncated.
- **Use:** Merge-queue CI saturation, `queue_batching` merge-rate comparisons, DRAIN **CI lane** hints.
- **Implementation note:** This is today’s `_fetch_queue_depth`; keep behavior until #3880 lands, then rename response fields with a deprecation window.

### 2. `rd_dispatch_queue_depth` (new — primary for agent backpressure)

- **Definition (v1):** Count of staff/agent coordination runs in **`queued` or `running`** status fleet-wide, optionally filtered to a repo when overload is evaluated per-repo (filter TBD in #3880 — default: fleet-wide count with per-repo overlay when `repo` is set).
- **Source:** Coordination staff active-run board (`coordination/staff_view` / existing staff run registry), not GitHub Actions.
- **Use:** `dispatch_queue_delay` alignment, future overload “agent pressure” reason codes, briefing lines that today imply RD depth.
- **Explicit non-goal for v1:** Do not include merge_group runs in this count.

### Latency companion (already exists)

Keep **`queue_p95_seconds`** from `dispatch_queue_delay.QueueDepthSnapshot` as the latency axis; depth without latency mis-fires under fast runners.

## Overload behavior (target state for #3880)

| Signal | Threshold owner | Action |
|--------|-----------------|--------|
| `github_merge_queue_run_depth` | `queue_batching` / ruleset `max_merge` | Batch/consolidation suggestions (existing) |
| `rd_dispatch_queue_depth` | New config alongside `open_count_threshold` (default TBD) | Optional DRAIN reason “dispatch backlog” (shadow mode first) |
| Open PR rate vs merge rate | `overload.py` windows | Unchanged |

**Shadow period:** Emit both metrics in overload snapshots with warnings when legacy field `merge_queue_depth` is read without checking labels; remove shadow after one release cycle.

## API / field naming

Proposed overload snapshot keys (subject to OpenAPI regen in #3880):

- `github_merge_queue_run_depth` (int)
- `rd_dispatch_queue_depth` (int | null when staff board unavailable)
- `queue_depth_warnings` (list[str]) — e.g. truncation, staff board degrade

Deprecate bare `merge_queue_depth` alias → same value as `github_merge_queue_run_depth` for one release.

## Observability & limits

- GitHub REST pagination: respect existing `gh_client` rate-limit handling; fail soft with warning strings (current pattern).
- Staff board: reuse coordination degrade paths; **null depth + warning** rather than guessing zero.
- Document staleness: overload snapshot `generated_at` remains authoritative; consumers must not assume sub-second freshness.

## Security & holds

- No new secrets; hub token scopes unchanged.
- HOLD #2657: metric work is code/docs only; no deploy-dependent validation required for this design PR.
- Do not use queue depth metrics to trigger **bulk queue cancels** (fleet policy).

## Open questions (for #3880 implementation)

1. Per-repo filter for `rd_dispatch_queue_depth`: exclude cross-repo agent runs or attribute by `repo` field on staff runs?
2. Should DRAIN mode ever latch on dispatch depth alone, or only as an additional reason when open-PR thresholds already fire?
3. GraphQL merge-queue **entry** count (M1) as a third labeled metric for escalation lane docs — defer unless escalation runbook requires it.

## References

- Panel synthesis: Paperclip vs RD `th_2a18c8a3116d` (2026-11-02)
- Code: `backend/coordination/overload.py`, `backend/dispatch_queue_delay.py`, `backend/routers/queue.py`
- Runbooks: `docs/runbooks/queue-stuck.md`, `docs/runbooks/escalation-lane.md`
