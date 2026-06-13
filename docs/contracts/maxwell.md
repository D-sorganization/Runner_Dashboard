# Maxwell-Daemon API Contract — Dashboard Consumer View

**Contract version**: 2.0.0 (matches Maxwell-Daemon `CONTRACT_VERSION`)  
**Date**: 2026-06-12  
**Issues**: [#366](https://github.com/D-sorganization/Runner_Dashboard/issues/366),
[#955](https://github.com/D-sorganization/Runner_Dashboard/issues/955),
[#956](https://github.com/D-sorganization/Runner_Dashboard/issues/956),
[#958](https://github.com/D-sorganization/Runner_Dashboard/issues/958)

> The `version`/`status`/`workers` shapes below now mirror the daemon's REAL
> response shapes (`maxwell_daemon/api/contract.py`). The discriminating field of
> each is **required**, so contract drift fails loudly (a `ValidationError` the
> proxy surfaces as `502`) instead of silently defaulting to "unknown / empty".

---

## Overview

The dashboard proxies requests to Maxwell-Daemon and applies a strict schema
at the boundary. Only the fields listed in this document are forwarded to
the frontend. Unknown fields from Maxwell are silently dropped. Sensitive
fields (see §Sensitive Field Blocklist) are explicitly excluded from every
model.

This contract is implemented in `backend/maxwell_contract.py`.

---

## Endpoints and Response Shapes

### `GET /api/maxwell/version`

Proxy of Maxwell-Daemon `/api/version`, which returns `{daemon, contract}`.

| Field                 | Type     | Notes                                                              |
| --------------------- | -------- | ------------------------------------------------------------------ |
| `daemon`              | `string` | **Required.** Daemon semantic version (MD `daemon` field)          |
| `contract`            | `string` | **Required.** MD surface contract version, e.g. `"2.0.0"`          |
| `version`             | `string` | Mirrors `daemon` for the existing frontend                         |
| `contract_compatible` | `bool`   | `false` on a major-version mismatch vs `EXPECTED_CONTRACT_VERSION` |

Version negotiation (#956): `GET /api/maxwell/status` also surfaces a
`contract` block `{expected, daemon, compatible}` when the daemon is reachable,
so the Maxwell tab can show a degraded-mode banner on an incompatible major
version instead of rendering defaulted data.

---

### `GET /api/maxwell/daemon-status` · `GET /api/maxwell/pipeline-state`

Proxy of Maxwell-Daemon `/api/status` (shape `{pipeline_state, active_task_id,
gate, sandbox}`), enriched with task counts from `/api/v2/status` (`counts` map).

| Field             | Type      | Notes                                                               |
| ----------------- | --------- | ------------------------------------------------------------------- |
| `pipeline_state`  | `string`  | **Required.** MD field: `"idle"`/`"running"`/`"paused"`/`"error"`   |
| `active_task_id`  | `string?` | MD field                                                            |
| `gate`            | `string?` | MD admission gate: `"open"`/`"closed"`                              |
| `sandbox`         | `string?` | `"enabled"`/`"disabled"`                                            |
| `state`           | `string`  | Mirrors `pipeline_state` for the existing frontend                  |
| `active_tasks`    | `int`     | From `/api/v2/status` `counts.running` (else 1 if a task is active) |
| `queued_tasks`    | `int`     | From `counts.queued`/`counts.pending`                               |
| `completed_tasks` | `int?`    | From `counts.completed`/`counts.done`                               |
| `failed_tasks`    | `int?`    | From `counts.failed`/`counts.error`                                 |
| `uptime_seconds`  | `float?`  |                                                                     |
| `last_activity`   | `string?` | ISO 8601                                                            |
| `paused`          | `bool`    | Derived: `pipeline_state == "paused"`                               |

The `/api/v2/status` enrichment is best-effort: if it is unavailable or
shape-shifted, the base `/api/status` mapping is still returned (`active_tasks`
derived from `active_task_id`).

---

### `GET /api/maxwell/tasks`

Proxy of Maxwell-Daemon `/api/tasks`.

| Field    | Type         | Notes                      |
| -------- | ------------ | -------------------------- |
| `tasks`  | `TaskItem[]` | See task item schema below |
| `cursor` | `string?`    | Opaque pagination cursor   |
| `total`  | `int?`       |                            |

**TaskItem**:

| Field        | Type       | Notes    |
| ------------ | ---------- | -------- |
| `id`         | `string`   | UUID     |
| `status`     | `string`   |          |
| `created_at` | `string?`  | ISO 8601 |
| `updated_at` | `string?`  | ISO 8601 |
| `type`       | `string?`  |          |
| `priority`   | `int?`     |          |
| `tags`       | `string[]` |          |
| `error`      | `string?`  |          |

---

### `GET /api/maxwell/tasks/{task_id}`

Proxy of Maxwell-Daemon `/api/tasks/{id}`.

Same as TaskItem plus:

| Field            | Type      | Notes             |
| ---------------- | --------- | ----------------- |
| `started_at`     | `string?` | ISO 8601          |
| `completed_at`   | `string?` | ISO 8601          |
| `result_summary` | `string?` | Truncated summary |

---

### `POST /api/maxwell/dispatch`

Proxy of Maxwell-Daemon `POST /api/v1/tasks`.

| Field             | Type      | Notes                       |
| ----------------- | --------- | --------------------------- |
| `task_id`         | `string`  | Returned as `id` by Maxwell |
| `status`          | `string`  | Typically `"queued"`        |
| `idempotency_key` | `string?` |                             |
| `created_at`      | `string?` |                             |
| `message`         | `string?` |                             |

---

### `POST /api/maxwell/pipeline-control/{action}`

Proxy of Maxwell-Daemon `POST /api/control/{action}` (issue #952; the daemon has
no `/api/v1/control/*` route).

| Field            | Type      | Notes                                     |
| ---------------- | --------- | ----------------------------------------- |
| `action`         | `string`  | `pause`, `resume`, `abort` (required)     |
| `applied_at`     | `string?` | MD timestamp when the control was applied |
| `previous_state` | `string?` | MD pipeline state before this action      |
| `status`         | `string`  | Legacy/optional, default `"ok"`           |
| `message`        | `string?` | Legacy/optional                           |

---

### `GET /api/maxwell/backends`

Proxy of Maxwell-Daemon `/api/v1/backends`.

| Field      | Type            | Notes |
| ---------- | --------------- | ----- |
| `backends` | `BackendItem[]` |       |

**BackendItem**:

| Field     | Type      | Notes                            |
| --------- | --------- | -------------------------------- |
| `name`    | `string`  | Display name, e.g. `"Anthropic"` |
| `type`    | `string`  |                                  |
| `enabled` | `bool`    |                                  |
| `model`   | `string?` |                                  |
| `status`  | `string?` |                                  |

> ⚠️ **`api_key`, `connection_string`, and similar fields are NEVER forwarded.**

---

### `GET /api/maxwell/workers`

Proxy of Maxwell-Daemon `/api/v1/workers`, which returns `{worker_count,
queue_depth}` (it does NOT yet emit per-worker items).

| Field          | Type           | Notes                                                       |
| -------------- | -------------- | ----------------------------------------------------------- |
| `worker_count` | `int`          | **Required.** Number of active workers (MD field)           |
| `queue_depth`  | `int?`         | Pending queue depth (MD field)                              |
| `total`        | `int?`         | Mirrors `worker_count` for the existing frontend            |
| `workers`      | `WorkerItem[]` | Empty until MD enriches the endpoint with per-worker detail |

**WorkerItem** (reserved for when MD adds per-worker detail):

| Field             | Type      |
| ----------------- | --------- |
| `id`              | `string`  |
| `status`          | `string`  |
| `current_task_id` | `string?` |
| `tasks_completed` | `int?`    |
| `tasks_failed`    | `int?`    |
| `started_at`      | `string?` |
| `last_activity`   | `string?` |

---

### `GET /api/maxwell/cost`

Proxy of Maxwell-Daemon `/api/v1/cost`.

| Field        | Type               |
| ------------ | ------------------ |
| `total_usd`  | `float?`           |
| `window`     | `string?`          |
| `by_model`   | `dict[str,float]?` |
| `by_backend` | `dict[str,float]?` |
| `currency`   | `string`           |

---

## Sensitive Field Blocklist

The following keys are stripped from ALL Maxwell responses before
model validation (defence-in-depth, `strip_sensitive()`):

- `secret_token`
- `api_key`
- `api_secret`
- `token`
- `password`
- `private_key`
- `connection_string`
- `db_url`
- `webhook_secret`
- `signing_secret`
- `client_secret`

---

## Versioning Policy

- This contract is versioned. Breaking changes (field removal, rename)
  require a version bump in this document and in `maxwell_contract.py`.
- Maxwell may add new fields freely; the dashboard will silently ignore them
  (Pydantic `extra=ignore` default).
- The dashboard must **not** depend on any field not listed here.
