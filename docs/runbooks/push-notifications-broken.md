# Runbook: Push Notifications Broken

The dashboard's web-push notification path (subscribe / send / test) is not
delivering alerts. Operators stop receiving incident pings even though the
dashboard itself appears healthy.

## Symptom

- A subscribed device stops receiving push notifications.
- The "Send test push" button in the dashboard returns a non-2xx or returns
  2xx but the device never receives the push.
- `journalctl -u runner-dashboard` shows repeated push-related errors
  (e.g. `UnconfiguredPushTransport`, `410 Gone`, `invalid VAPID`).
- A new operator cannot subscribe a device — `POST /api/push/subscribe`
  returns 4xx.

## Severity

**P2** by default (operators lose proactive paging but the dashboard UI
still surfaces incidents).
**P1** if push is the only paging path configured for an on-call rotation.

## Detection

- Operators report no test push received.
- `curl -fsS http://localhost:8321/api/push/vapid-public-key` returns empty
  or errors.
- Backend logs show traffic to `backend/push.py` ending in non-2xx.
- The push transport reports "unconfigured" — i.e. the
  `UnconfiguredPushTransport` class in `backend/push.py` is in use.

## Diagnosis

```bash
# 1. Confirm the public VAPID key is being served.
curl -fsS http://localhost:8321/api/push/vapid-public-key | jq '.'

# 2. Send a test push to a known subscription via the dashboard endpoint.
#    (Replace <topic> with a valid topic the device is subscribed to.)
curl -fsS -X POST http://localhost:8321/api/push/test \
  -H 'Content-Type: application/json' \
  -d '{"topic":"<topic>","title":"runbook test","body":"hello"}'

# 3. Inspect the push subscription store (sqlite, path resolved by
#    backend/push.py::_db_path()).
SUBS_DB=$(python3 -c "from backend.push import _db_path; print(_db_path())")
sqlite3 "$SUBS_DB" 'SELECT id, endpoint, topics, created_at FROM subscriptions LIMIT 10;'

# 4. Tail recent push-related log lines.
sudo journalctl -u runner-dashboard --since "1 hour ago" --no-pager \
  | grep -Ei 'push|vapid|subscription|410|401'

# 5. Confirm the configured transport is not the unconfigured stub.
curl -fsS http://localhost:8321/api/health | jq '.push // .components.push'
```

## Mitigation

```bash
# A. If the VAPID keys are missing or rotated, rebuild and restart.
#    Update the env file with VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY values.
sudoedit ~/.config/runner-dashboard/env
sudo systemctl restart runner-dashboard
curl -fsS http://localhost:8321/api/push/vapid-public-key | jq '.'

# B. If individual subscriptions return 410 Gone, the device unsubscribed.
#    backend/push.py::_delete_stale_subscription removes them automatically;
#    if it failed, prune manually:
sqlite3 "$SUBS_DB" "DELETE FROM subscriptions WHERE last_error_code = 410;"

# C. If the transport is the unconfigured stub, set a real one.
#    In production, configure pywebpush via env, then:
sudo systemctl restart runner-dashboard

# D. Re-subscribe affected devices (tap "Enable notifications" again in
#    the dashboard UI). The browser will issue a fresh subscription.
```

## Resolution

- If VAPID keys leaked or expired, treat as a credential rotation
  (cross-reference `secrets-compromise.md`) and re-issue subscriptions.
- If the bug is in `backend/push.py`, add a pydantic-validated regression
  test under `tests/api/test_push.py` that exercises the failing path
  (subscribe, send, validate stale-pruning).
- If browsers return 410 in bursts, confirm `_delete_stale_subscription` is
  wired into the failure path and adds telemetry on prune count.
- If the transport is silently `UnconfiguredPushTransport` in production,
  add a startup health check that fails fast when push is required but
  unconfigured.
- Update operator docs to confirm browser permissions and device-side
  notification settings as a first triage step.

## Postmortem Template

[`postmortem-template.md`](./postmortem-template.md)
