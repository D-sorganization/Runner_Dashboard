# Code Requests

**Status:** Active  
**Epic:** #1279  
**Specification:** CR-2 (#1282), CR-1 (#1281)

## Overview

Code Requests provide typed, auditable engineering tasks across the **D-sorganization** fleet. Rather than storing requests purely in an ephemeral node-local JSON file, each Code Request has a durable, authoritative record in **GitHub Issues** within its target repository.

A node-local JSON file (`~/actions-runners/dashboard/code_requests.json`) functions strictly as an accelerator cache and can be rebuilt on-demand from GitHub at any time without data loss.

---

## Canonical GitHub Record

Each Code Request is stored as an issue in the target repository:

- **Labels:** `code-request` and `code-request:<state>`
- **Body:** Machine-readable YAML front-matter block at the top, followed by the human-readable prompt.

### Front-Matter Example

````markdown
```yaml
id: cr-Runner_Dashboard-101
repository: Runner_Dashboard
state: triage
board_route: auto
board_proposal: null
plan_epic: null
planner_profile_id: planner-strong
executor_profile_id: claude-3-7-sonnet
requester:
  id: dieterolson
  kind: human
standards:
  - tdd
  - dbc
branch: main
created_at: "2026-09-25T12:00:00Z"
updated_at: "2026-09-25T12:05:00Z"
```
````

## Prompt

Implement kinematic velocity validation in swing analysis pipeline.

```

---

## Lifecycle State Machine

The lifecycle is modeled as a pure state transition function (`code_requests.lifecycle.transition`).

```

                ┌───────► board_review ──┬──► deferred
                │             │          └──► declined

draft ──► triage┤ ▼
└───────► planning ──► planned ──► executing ──┬──► done
└──► failed

Operator Overrides:

- Any pre-planning state (draft, triage, board_review) ──► planning | board_review
- Any state ──► cancelled

````

### Transition Invariants
- Illegal transitions immediately raise `InvalidTransitionError`.
- Every transition appends an audit record to the request's `audit_trail`.
- Transitions post an audit comment to the GitHub issue:
  `**[Code Request]** State changed from `<from>` to `<to>` by `<actor>`.`
- Transitions record an event in `backend/dispatch/audit.py` (and `dispatch_audit.ndjson`).
- Labels on GitHub are updated atomically (`code-request:<old>` removed, `code-request:<new>` added).

---

## REST API Endpoints

All endpoints are mounted on `/api/code-requests`:

### 1. `GET /api/code-requests`
- **Scope / Auth:** `require_fleet_peer` / session auth.
- **Response:**
  ```json
  {
    "requests": [ ... ],
    "total": 1,
    "dispatchTarget": { "workflow": "...", "available": true, "detail": "" }
  }
````

### 2. `GET /api/code-requests/{id}`

- **Scope / Auth:** `require_fleet_peer` / session auth.
- **Lookup:** By `id` (e.g. `cr-Runner_Dashboard-101`), `repo#issue_number`, or issue number.
- **Response:** Full `CodeRequest` payload including audit trail.

### 3. `POST /api/code-requests`

- **Scope / Auth:** `code-requests.manage` (with backward-compatible alias `feature-requests.manage`).
- **Body:**
  - `repository` (string, required)
  - `prompt` (string, required)
  - `state` (`draft` or `triage`, default `draft`)
  - `submitted` (boolean, if true creates in `triage`)
  - `standards` (list of strings: `tdd`, `dbc`, `dry`, `lod`, `security`, `docs`)
  - `board_route` (`auto`, `force_board`, `skip_board`)

### 4. `POST /api/code-requests/{id}/transition`

- **Scope / Auth:** `code-requests.manage`.
- **Body:**
  - `to_state` (string, required)
  - `reason` (string, required)
  - `is_operator_override` (boolean, optional)

---

## Label Management

Labels are provisioned across the fleet using `scripts/ensure_code_request_labels.py`:

```bash
python scripts/ensure_code_request_labels.py --repo Runner_Dashboard
```
