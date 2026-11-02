Board priority 11-02 item 3.

## Problem

The agent_bus stale-session reaper (`backend/agent_bus/reaper.py`) releases tracked GitHub claims when a session goes stale. On a **remote fleet node** that is unhealthy (`/readyz` aggregate not ok), cross-host principals must **not** have claims reaped prematurely—only local-node principals use local readyz today.

## Scope

**In:** Before `release_tracked_claims`, gate on host readyz for principals registered on another node; unit tests with mocked health.

**Out:** Changing lease TTL semantics; reaper interval; GitHub claim protocol (#2765).

## Files likely touched

- `backend/agent_bus/reaper.py`
- `backend/agent_bus/store.py` (if principal→node mapping needed)
- `backend/readiness.py` or fleet health client for remote readyz
- `tests/api/test_agent_bus_slice3.py` or new `tests/unit/test_agent_bus_reaper_readyz.py`

## Acceptance criteria

- [ ] Unhealthy remote node: stale session retained, claims **not** released (test).
- [ ] Healthy remote node: existing reaper behavior unchanged (test).
- [ ] Local-node principals still use local readyz (test).
- [ ] PR &lt;500 lines, TDD; `Closes #3823`.

## Test plan (TDD)

1. Red: mock store + readyz → assert zero releases when remote unhealthy.
2. Red: healthy remote → releases as today.
3. Green: implement gate in `reap_once`.

## PR split

One PR (&lt;500 lines).
