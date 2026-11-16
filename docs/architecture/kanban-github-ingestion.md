# Kanban GitHub ingestion (#4319)

Epic #4317 slice 2. Builds GitHub slices of
`KanbanWorkItemSnapshot` and runs `project_kanban_column` — no card storage.

Implementation: `backend/kanban_github_ingestion.py`, route `GET /api/kanban/github`.

## Cache and budget

| Knob | Default | Env override |
| --- | --- | --- |
| Board TTL | 60s | `KANBAN_GITHUB_CACHE_TTL_SECONDS` |
| Cold sync API budget | 40 calls | `KANBAN_GITHUB_COLD_BUDGET` |
| Warm sync API budget | 5 calls | `KANBAN_GITHUB_WARM_BUDGET` |

Issue lists use GitHub `Link` pagination (same semantics as `gh_client.paginate`).
A warm sync returns the cached board when TTL has not expired (0 GitHub calls).

## Freshness

`last_sync` and per-source freshness (`issues`, `pull_requests`, `checks`, `reviews`,
`auto_merge`) are on every API response. Degraded or rate-limited reads mark the affected
snapshot fields `available=false` / `degraded=true` so projection stays fail-closed
(`unknown` column).

Claims (#4320) and holds (#4321) are not populated in this slice.
