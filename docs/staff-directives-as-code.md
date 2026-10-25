# Directives as code (Repository_Management)

Operator directives can be declared in Repository_Management and reconciled into
dashboard state by the same RM role-sync timer that fast-forwards
`STAFF_RM_ROOT` (`python -m staff.rm_sync`, `runner-dashboard-rm-sync.service`).

## Source file

| Field    | Location                                      |
| -------- | --------------------------------------------- |
| YAML     | `staff/directives.yml` under `STAFF_RM_ROOT`  |
| Persisted| `staff_directives.json` (`STAFF_DIRECTIVES_FILE`) |

Example:

```yaml
directives:
  - text: Finish the coordination API first
    repo: Runner_Dashboard
    priority: 1
    expires: 2026-12-31T23:59:59Z
    set_by: dieter
```

- `text` — single line, 1–500 characters (same rules as the PUT API).
- `repo` — `*` (default) or a bare repository name.
- `priority` — 1 (highest) through 5, default 3.
- `expires` — optional ISO-8601 timestamp (UTC `Z` suffix recommended).
- `set_by` — attribution stored on the directive (audit rows for sync use principal `role-sync`).

## Reconcile behaviour

On each successful RM refresh (including throttled passes):

1. YAML directives are merged into the persisted list with `managed: true`.
2. Managed directives removed from YAML are **deactivated** (`active: false`), never deleted.
3. Directives past `expires` are marked inactive on sync.
4. Directives created through `PUT /api/priorities/directives` remain `managed: false` and are not removed by sync.
5. A YAML syntax error in `staff/directives.yml` fails closed (no writes); role YAML parse errors also block hold reconcile.

Dry-run: `STAFF_RM_RECONCILE_DRY_RUN=1` or `python -m staff.rm_sync --dry-run`.

## HTTP writes (operators)

Reads stay on the fleet peer contract (`GET /api/priorities/directives`).

`PUT /api/priorities/directives` still replaces the full list but requires a
principal with `priorities.write` (operator preset or admin). The shared
`HUB_FLEET_TOKEN` and agent bot tokens cannot steer directives. Production hubs
should use GitHub OAuth operator sessions (see `docs/runbooks/node-b-production-oauth.md`);
loopback-only writes are development-only (issue #2068).

See also `docs/priorities-api.md` for request shapes and optimistic concurrency (`version`).
