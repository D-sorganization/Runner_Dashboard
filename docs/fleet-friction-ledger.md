# Fleet friction ledger and owner questions

The dashboard keeps the shell orchestrator's `FRICTION_LEDGER.md` and `OWNER_QUESTIONS.md` as queryable stores so fleet workflows can be monitored and improved over time (issue #2502).

## Friction ledger (`/api/v1/fleet/friction`)

Every slowdown seen in the fleet gets one row. Fields: `id` (`F<n>`), `title`, `seen`, `repo`, `fix`, `fix_ref` (PR or commit URL), `detector`, `detector_ref`, `status` (`open`, `in_progress`, `waiting`, `closed`), `created_by`, `created_at`, `updated_at`, `recurrence_count`, `last_seen_at`.

**Closing rule, enforced server-side:** `status=closed` needs a non-empty `fix_ref` (a merged or deployed fix) and a non-empty `detector` (a check that makes a recurrence visible). Otherwise the API answers 422, on create and on PATCH.

| Route                            | Auth                      | Purpose                                                                 |
| -------------------------------- | ------------------------- | ----------------------------------------------------------------------- |
| `GET /friction?status=&repo=`    | fleet peer (`fleet.read`) | List rows                                                               |
| `POST /friction`                 | `coordination.write`      | Create (needs `title` and `seen`)                                       |
| `PATCH /friction/{id}`           | `coordination.write`      | Update fields or status                                                 |
| `POST /friction/{id}/recurrence` | `coordination.write`      | Detectors call this: increments `recurrence_count`, sets `last_seen_at` |

## Owner questions (`/api/v1/fleet/owner-questions`)

Fields: `id` (`Q<n>`), `repo`, `issue`, `question`, `options[]`, `recommended`, `asked_by`, `asked_at`, `answer`, `answered_by`, `answered_at`, `status` (`open`, `answered`, `withdrawn`).

| Route                                 | Auth                                                   |
| ------------------------------------- | ------------------------------------------------------ |
| `GET /owner-questions?status=`        | fleet peer                                             |
| `POST /owner-questions`               | `coordination.write` (orchestrator and bot principals) |
| `POST /owner-questions/{id}/answer`   | `staff.admin` (operator or admin only; bots get 403)   |
| `POST /owner-questions/{id}/withdraw` | the asker, or an operator                              |

An answered or withdrawn question cannot change again (409).

## Where it shows up

- `GET /api/staff/summary` and `/api/v1/staff/summary` carry `friction_open` (rows not closed) and `owner_questions_open`.
- `GET /api/coordination/briefing` carries `fleet_open` with the same two counts.
- MCP/CLI tools: `fleet_friction_list`, `fleet_friction_report` (recurrence when `friction_id` is given, else create), `fleet_owner_question_ask`, `fleet_owner_questions_list`.

## Storage

SQLite at `$FLEET_FRICTION_DB`, default `<config dir>/fleet_friction.sqlite3`. Node-local, like the staff run store.

## Importing the existing ledger

```bash
python scripts/import_friction_ledger.py path/to/FRICTION_LEDGER.md          # dry run (default)
FLEET_TOKEN=... python scripts/import_friction_ledger.py path/to/FRICTION_LEDGER.md --apply
```

The importer maps the `CLOSED`, `OPEN`, `IN PROGRESS` and `WAITING` status prefixes, extracts PR references into `fix_ref`, and warns about closed rows that have no PR or no real detector. Run `--apply` once; rows are not de-duplicated.
