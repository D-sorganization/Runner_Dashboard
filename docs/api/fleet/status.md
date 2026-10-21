# GET /api/fleet/status

Fleet-wide system metrics aggregate. Implemented in `backend/routers/fleet.py` with
stale-while-revalidate caching in `backend/fleet_status_cache.py` (#2110).
