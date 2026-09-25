# Staff API v1 Specification (/api/v1/staff)

> **Context:** Part of **SC-F** (#1352) under the Staff Console umbrella (#1354). Issue #1312 (SC-F3).  
> **Status:** Stable / Current  
> **Predecessor:** `/api/staff` (deprecated with RFC 8594 sunset headers)

---

## 1. Overview & Principles

The Versioned Public Staff API (`/api/v1/staff`) provides a uniform, contract-tested API surface for Grok Bot, Claude Cowork, Codex, and the dashboard frontend.

### Core Architectural Guarantees
1. **Stable URL Namespace:** All public routes are prefixed with `/api/v1/staff`.
2. **Predictable Error Envelope:** Every `4xx` and `5xx` response adheres to a single schema containing machine-readable error codes and correlation IDs.
3. **24-Hour Idempotency:** Mutating endpoints (`POST`, `PUT`) require an `Idempotency-Key` header; retrying with an identical key within 24 hours safely replays the response without executing duplicate runs or operations.
4. **Keyset Cursor Pagination:** All collection endpoints support forward and backward pagination with stable, opaque base64 cursors.
5. **Fail-Closed Durability:** Any failure in the persistence layer (idempotency store, audit log) halts execution and returns a classified `503 Service Unavailable` rather than risking duplicate actions or unlogged state changes.

---

## 2. Legacy Migration & Deprecation

Legacy `/api/staff/...` routes remain available as aliases to avoid breaking existing clients during migration, but emit standard RFC 8594 deprecation and sunset headers:

```http
Deprecation: true
Sunset: Sun, 01 Nov 2026 00:00:00 GMT
Link: </api/v1/staff/roster>; rel="successor-version"
```

All new development and client tool integrations must use `/api/v1/staff/...`.

---

## 3. Error Envelope Contract

All `4xx` and `5xx` responses returned by `/api/v1/staff` conform strictly to the following JSON structure:

```json
{
  "error": {
    "code": "missing_idempotency_key",
    "message": "Mutating staff operation requires an 'Idempotency-Key' request header.",
    "retryable": false,
    "hint": "Include 'Idempotency-Key: <uuid>' in your request headers.",
    "request_id": "req-9c84e1837a4b"
  }
}
```

### Fields
| Field | Type | Description |
|---|---|---|
| `code` | `string` | Machine-readable error code (e.g. `unauthorized`, `forbidden`, `not_found`, `validation_error`, `missing_idempotency_key`, `invalid_cursor`, `idempotency_store_failure`). |
| `message` | `string` | Human-readable explanation of the error. |
| `retryable` | `boolean` | `true` if re-attempting the identical request may succeed (e.g. 503 transient lock contention); `false` if the request requires client-side correction. |
| `hint` | `string` | Actionable remediation advice or link to documentation. |
| `request_id` | `string` | Correlation identifier for distributed tracing and log inspection. |

---

## 4. Idempotency Guarantees

Mutating requests (`POST`, `PUT`) must include an `Idempotency-Key` header (recommended format: UUIDv4).

### Request Example
```http
POST /api/v1/staff/night-watch/run HTTP/1.1
Host: dashboard.local
Authorization: Bearer <token>
Idempotency-Key: 9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d
Content-Type: application/json

{
  "prompt": "Investigate CI failure on PR #1234",
  "provider": "claude"
}
```

### Idempotency Behavior
1. **First Execution:** The request executes normally and stores the response in the SQLite WAL-backed idempotency ledger (`idempotency_keys.sqlite3`).
2. **Replayed Execution:** Any request with the identical `(key, endpoint, principal)` received within 24 hours returns the cached response immediately, with the replay header:
   ```http
   Idempotent-Replay: true
   ```
3. **Store Failure:** If the idempotency store cannot record or look up a key (e.g., disk full or lock error), the request fails closed:
   ```json
   {
     "error": {
       "code": "idempotency_store_failure",
       "message": "Idempotency store lookup failed: disk I/O error",
       "retryable": true,
       "hint": "Retry after the database connection is restored; request aborted to prevent duplicates.",
       "request_id": "req-7b3e210a"
     }
   }
   ```

---

## 5. Keyset Cursor Pagination

List endpoints (`/api/v1/staff/runs`, `/api/v1/staff/audit`) support cursor pagination:

### Query Parameters
- `limit` (integer, 1–500, default: 50): Number of items per page.
- `cursor` (string, optional): Base64-encoded cursor token from a previous response.

### Response Shape
```json
{
  "items": [ ... ],
  "next_cursor": "cursor_token_next_page",
  "prev_cursor": "cursor_token_prev_page",
  "has_more": true
}
```

When `has_more` is `false`, `next_cursor` is `null`. Passing an invalid or tampered cursor yields a `400 Bad Request` with `code: "invalid_cursor"`.

---

## 6. Route Catalog & Scopes

| Method | Path | Required Scope | Idempotency Key | Description |
|---|---|---|---|---|
| `GET` | `/api/v1/staff/roster` | `staff.read` | No | Roster of registered staff roles and provider options |
| `GET` | `/api/v1/staff/board` | `staff.read` | No | Current fleet runs, queue status, active agents |
| `GET` | `/api/v1/staff/summary` | `staff.read` | No | Fleet summary and aggregate spend |
| `GET` | `/api/v1/staff/runs` | `staff.read` | No | Paginated list of historical and active runs |
| `GET` | `/api/v1/staff/runs/{id}` | `staff.read` | No | Run details, attempts, and event history |
| `GET` | `/api/v1/staff/runs/{id}/stream`| `staff.read` | No | SSE event stream for live log tailing |
| `POST`| `/api/v1/staff/{role}/run` | `staff.dispatch`| **Required** | Dispatch a staff execution or preview (`dry_run`) |
| `POST`| `/api/v1/staff/runs/{id}/cancel`| `staff.cancel` | **Required** | Cancel an active or queued run |
| `GET` | `/api/v1/staff/audit` | `staff.audit.read`| No | Durable append-only audit trail (paginated) |
| `GET` | `/api/v1/staff/schedule` | `staff.read` | No | Scheduled roles and calendar triggers |
| `GET` | `/api/v1/staff/holds` | `staff.read` | No | Current execution holds |
| `PUT` | `/api/v1/staff/holds` | `staff.holds.write`| **Required** | Set or replace execution holds |
| `GET` | `/api/v1/staff/usage` | `staff.read` | No | Token and cost usage metrics |
| `GET` | `/api/v1/staff/usage/pricing`| `staff.read` | No | Model pricing lookup table |
| `POST`| `/api/v1/staff/usage/export` | `staff.admin` | **Required** | Export usage metrics to Repository_Management |
| `POST`| `/api/v1/staff/threads` | `staff.chat` | No | Create conversation thread (direct, group, auto to Barb) |
| `GET` | `/api/v1/staff/threads` | `staff.read` | No | Paginated list of threads with filters (`role`, `status`, `unread`) |
| `GET` | `/api/v1/staff/threads/{id}` | `staff.read` | No | Thread details and message history |
| `PATCH`| `/api/v1/staff/threads/{id}`| `staff.chat` | No | Rename thread title or archive thread status |
| `POST`| `/api/v1/staff/threads/{id}/messages` | `staff.chat` | **Required** | Post user message; returns 202 Accepted with reply placeholder |
| `GET` | `/api/v1/staff/threads/{id}/stream` | `staff.read` | No | SSE stream with Last-Event-ID resume, heartbeats, token events |
| `POST`| `/api/v1/staff/threads/{id}/read` | `staff.chat` | No | Mark thread read for caller principal |
| `GET` | `/api/v1/staff/inbox` | `staff.read` | No | List open threads requiring user attention (unread or pending actions) |

---

## 7. Conversations & Threads API (SC-B3)

The Conversation & Threads API enables interactive conversations between operators and staff roles (Barb, Night Watch, PR Remediator), action proposal review workflows, and live SSE event streaming.

### Message Submission (202 Accepted & Idempotency)
Posting a message to `POST /api/v1/staff/threads/{id}/messages` requires an `Idempotency-Key` header and returns `202 Accepted` immediately with:
1. The persisted user message record (state: `complete`).
2. A pending reply placeholder record (state: `pending`, `meta.in_reply_to: <user_message_id>`).

Replaying a request with the same `Idempotency-Key` returns `200 OK` with header `Idempotent-Replay: true` and the existing message records, guaranteeing zero duplicate agent responses.

### SSE Stream & Resume Contract (`/api/v1/staff/threads/{id}/stream`)
The Server-Sent Events stream delivers live updates for:
- `message`: user messages, agent completions, or error messages with failure classification.
- `token`: streaming delta tokens from active model runs.
- `proposal`: created or transitioned action proposals.
- `run_card`: execution status updates for triggered runs.

#### Resume via `Last-Event-ID`
Clients passing `Last-Event-ID: <seq>` or query parameter `since_seq=<seq>` receive an immediate sequential replay of all missed messages from SQLite before live events begin.

#### Connection Maintenance
- Heartbeats (`: keep-alive\n\n`) emit every 15 seconds of silence.
- Query parameter `follow=false` allows single-batch catch-up without keeping the connection open.

