# Maxwell-Daemon API Contract — Dashboard Consumer View

**Contract version**: v1  
**Date**: 2026-04-30  
**Issue**: [#366](https://github.com/D-sorganization/Runner_Dashboard/issues/366)

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

Proxy of Maxwell-Daemon `/api/version`.

| Field          | Type         | Notes                          |
|----------------|--------------|--------------------------------|
| `version`      | `string`     | Semantic version, default `"unknown"` |
| `build`        | `string?`    | CI build label or hash         |
| `environment`  | `string?`    | e.g. `"production"`            |
| `started_at`   | `string?`    | ISO 8601 daemon start time     |

---

### `GET /api/maxwell/daemon-status` · `GET /api/maxwell/pipeline-state`

Proxy of Maxwell-Daemon `/api/status`.

| Field             | Type       | Notes                           |
|-------------------|------------|---------------------------------|
| `state`           | `string`   | `"idle"`, `"running"`, …        |
| `active_tasks`    | `int`      | Currently executing             |
| `queued_tasks`    | `int`      | Waiting in queue                |
| `completed_tasks` | `int?`     |                                 |
| `failed_tasks`    | `int?`     |                                 |
| `uptime_seconds`  | `float?`   |                                 |
| `last_activity`   | `string?`  | ISO 8601                        |
| `paused`          | `bool`     |                                 |

---

### `GET /api/maxwell/tasks`

Proxy of Maxwell-Daemon `/api/tasks`.

| Field    | Type            | Notes                       |
|----------|-----------------|-----------------------------|
| `tasks`  | `TaskItem[]`    | See task item schema below  |
| `cursor` | `string?`       | Opaque pagination cursor    |
| `total`  | `int?`          |                             |

**TaskItem**:

| Field          | Type       | Notes                     |
|----------------|------------|---------------------------|
| `id`           | `string`   | UUID                      |
| `status`       | `string`   |                           |
| `created_at`   | `string?`  | ISO 8601                  |
| `updated_at`   | `string?`  | ISO 8601                  |
| `type`         | `string?`  |                           |
| `priority`     | `int?`     |                           |
| `tags`         | `string[]` |                           |
| `error`        | `string?`  |                           |

---

### `GET /api/maxwell/tasks/{task_id}`

Proxy of Maxwell-Daemon `/api/tasks/{id}`.

Same as TaskItem plus:

| Field            | Type       | Notes              |
|------------------|------------|--------------------|
| `started_at`     | `string?`  | ISO 8601           |
| `completed_at`   | `string?`  | ISO 8601           |
| `result_summary` | `string?`  | Truncated summary  |

---

### `POST /api/maxwell/dispatch`

Proxy of Maxwell-Daemon `POST /api/v1/tasks`.

| Field             | Type       | Notes                        |
|-------------------|------------|------------------------------|
| `task_id`         | `string`   | Returned as `id` by Maxwell  |
| `status`          | `string`   | Typically `"queued"`         |
| `idempotency_key` | `string?`  |                              |
| `created_at`      | `string?`  |                              |
| `message`         | `string?`  |                              |

---

### `POST /api/maxwell/pipeline-control/{action}`

Proxy of Maxwell-Daemon `POST /api/v1/control/{action}`.

| Field     | Type      | Notes                        |
|-----------|-----------|------------------------------|
| `action`  | `string`  | `pause`, `resume`, `abort`   |
| `status`  | `string`  | Default `"ok"`               |
| `message` | `string?` |                              |

---

### `GET /api/maxwell/backends`

Proxy of Maxwell-Daemon `/api/v1/backends`.

| Field       | Type             | Notes                     |
|-------------|------------------|---------------------------|
| `backends`  | `BackendItem[]`  |                           |

**BackendItem**:

| Field     | Type      | Notes                              |
|-----------|-----------|------------------------------------|
| `name`    | `string`  | Display name, e.g. `"Anthropic"`  |
| `type`    | `string`  |                                    |
| `enabled` | `bool`    |                                    |
| `model`   | `string?` |                                    |
| `status`  | `string?` |                                    |

> ⚠️ **`api_key`, `connection_string`, and similar fields are NEVER forwarded.**

---

### `GET /api/maxwell/workers`

Proxy of Maxwell-Daemon `/api/v1/workers`.

| Field       | Type            |
|-------------|-----------------|
| `workers`   | `WorkerItem[]`  |
| `total`     | `int?`          |

**WorkerItem**:

| Field               | Type      |
|---------------------|-----------|
| `id`                | `string`  |
| `status`            | `string`  |
| `current_task_id`   | `string?` |
| `tasks_completed`   | `int?`    |
| `tasks_failed`      | `int?`    |
| `started_at`        | `string?` |
| `last_activity`     | `string?` |

---

### `GET /api/maxwell/cost`

Proxy of Maxwell-Daemon `/api/v1/cost`.

| Field        | Type              |
|--------------|-------------------|
| `total_usd`  | `float?`          |
| `window`     | `string?`         |
| `by_model`   | `dict[str,float]?`|
| `by_backend` | `dict[str,float]?`|
| `currency`   | `string`          |

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
