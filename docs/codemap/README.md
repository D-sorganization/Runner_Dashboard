# Runner_Dashboard codemap

Silent Cartographer owns this directory. Read [`../codemap.md`](../codemap.md) first, then
[`repo-map.md`](repo-map.md) and [`../architecture/C4.md`](../architecture/C4.md).

## Refresh

```bash
python scripts/codemap_refresh.py          # rewrite repo-map + freshness-report.json
python scripts/codemap_refresh.py --check-only   # exit 1 when stale_sections non-empty
```

`freshness-report.json` records `generated_at`, `commit_sha`, and `stale_sections` (for example
when `docs/architecture/` lags `backend/server.py`).

Role wiring: RM `staff/roles/cartographer.yml`, RD `backend/staff/cartographer_persona.py`,
pack sources in `backend/knowledge_pack/cartographer.py`.
