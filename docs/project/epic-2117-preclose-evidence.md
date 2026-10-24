## Pre-close evidence gate — epic #2117 (verified on `main` 2026-10-24)

Sub-issues **#2118–#2123** are closed. Below maps each acceptance checkbox to paths, tests, and merge PRs. **Epic stays open** — epic-level gaps at the end.

---

### Slice #2118 — GitHub collector port (merged **PR #2124**)

| Criterion | Evidence |
| --- | --- |
| `delivery_metrics` package with frozen models | `backend/delivery_metrics/models.py` (`PrSnapshot`, `CheckRunSnapshot`) |
| `GitHubDeliveryCollectorPort` + `GhClientDeliveryCollector` via `gh_client` | `backend/delivery_metrics/ports.py`, `backend/delivery_metrics/adapters/github.py` |
| Incremental PR list + check runs; bounded concurrency | `list_pulls_updated_since`, `list_check_runs`, `asyncio.Semaphore` in `adapters/github.py` |
| Cache keys use auth fingerprint; `GhRateLimited` propagates | `_cache_prefix()` / `get_auth_fingerprint()`; `test_rate_limit_propagates` |
| Real fixtures, no live GitHub in CI | `tests/fixtures/delivery_metrics/`; `tests/unit/test_delivery_metrics_parse.py`, `test_delivery_metrics_github_collector.py` |

---

### Slice #2119 — Store + sync (merged **PR #2135**; also #2128, #2131)

| Criterion | Evidence |
| --- | --- |
| Schema + per-repo sync cursor | `backend/delivery_metrics/db.py` (`init_db`, `get_repo_sync_cursor`, upserts) |
| `collect_delivery_metrics_once()` idempotent | `backend/delivery_metrics/sync.py`; `test_collect_once_is_idempotent` in `tests/unit/test_delivery_metrics_store_sync.py` |
| Lifespan sync loop behind feature flag | `backend/server.py` (`start_delivery_metrics_sync_loop`); `delivery_metrics_enabled()` in `config.py` |
| In-memory DB + fake port tests | `test_sync_cursor_advances`, `test_upsert_pull_detects_change` |

---

### Slice #2120 — Rollups (merged **PR #2139**)

| Criterion | Evidence |
| --- | --- |
| Pure aggregators, no GitHub I/O | `backend/delivery_metrics/rollup.py`, composed in `report.py` |
| CI rounds, top failing checks, lead/ci wait, churn flags | `ci_rounds_to_green`, `top_failing_checks`, `build_pr_rollup`, `churn_flags` |
| Weekly trend buckets | `weekly_trends()` in `rollup.py`; `test_build_metrics_report_from_seeded_db` |
| Drill-down payloads with PR numbers + check run ids | `pr_drill_down()` returns `pull`, `rollup`, `check_runs` (`check_run_id` in models) |
| **Grouping by role/agent, repo, model** | **Not implemented** — only repo filter in UI; no backend grouping dimensions |
| **Escapes weighted (red-main, reverts, rollouts)** | **Stub** — `aggregate_window` sets `"escapes_weighted": []` always (`rollup.py`) |

---

### Slice #2121 — Read API (merged **PR #2140**)

| Criterion | Evidence |
| --- | --- |
| `/api/v1/metrics/*` + `fleet.read` + default-off flag | `backend/routers/delivery_metrics.py`; `DASHBOARD_ENABLE_DELIVERY_METRICS` in `config.py` |
| 404 when flag off | `test_summary_404_when_flag_off` in `tests/api/test_delivery_metrics_api.py` |
| Summary, trends, weekly-summary, pull drill-down | routes `summary`, `trends`, `weekly-summary`, `pull` |
| OpenAPI updated | `frontend/src/lib/openapi.json` (`delivery-metrics` tags) |

---

### Slice #2122 — Frontend (merged **PR #2144**)

| Criterion | Evidence |
| --- | --- |
| Insights tab panel behind API flag (404 → disabled UI) | `frontend/src/pages/DeliveryMetrics/DeliveryMetricsPanel.tsx`; wired in `Analysis.tsx` |
| Trends + repo filter + PR drill-down | `useDeliveryMetrics.ts`, `deliveryMetricsApi.ts` |
| Anti-leaderboard copy | `data-testid="delivery-metrics-posture"` |
| Vitest empty/error/disabled | `frontend/src/pages/DeliveryMetrics/__tests__/deliveryMetrics.test.tsx` |

---

### Slice #2123 — Weekly summary + staff attribution (merged **PR #2145**)

| Criterion | Evidence |
| --- | --- |
| `GET /api/v1/metrics/weekly-summary` stable JSON + markdown | `build_weekly_summary`, `render_weekly_summary_markdown` in `weekly_summary.py`; `test_weekly_summary` (API) |
| Dieter wait from proposals | `dieter_wait_seconds()`; `test_dieter_wait_sums_human_proposal_decisions` |
| Cost per merged PR from session telemetry | `staff["cost_per_merged_pr"]` from `get_session_telemetry_store()` |
| Optional weekly artifact hook | `backend/delivery_metrics/weekly_artifact.py`; `test_weekly_marker_is_stable` |

---

### Epic acceptance criteria (#2117 body)

| Epic checkbox | Verdict | Notes |
| --- | --- | --- |
| Incremental GitHub collection + cache + rate limits + existing auth | **Pass** | #2118 evidence |
| Metrics: CI rounds, failing checks, lead time, Dieter wait, churn, cost | **Partial** | Dieter wait + cost in weekly summary; per-PR `dieter_wait_seconds` always `None` in rollups; **rework** not modeled |
| **Escapes** (red main, reverts, failed rollouts) | **Fail** | `escapes_weighted` always `[]` |
| `GET /api/v1/metrics/*` + dashboard page, default-off flag | **Pass** | #2121, #2122 |
| Weekly summary for staff/board | **Pass** | #2123 |
| Drill-down to PRs and workflow runs; no vanity leaderboards | **Partial** | PR + **check runs** drill-down; not Actions workflow-run IDs; UI avoids leaderboards |

---

### Decision

**Do not close epic #2117** until escapes rollups and role/agent/model grouping (slice #2120 / epic scope) land with tests. Track follow-up on top of merged slice PRs above.

*Evidence run: cloud agent housekeeping PR (STATUS refresh + this comment).*
