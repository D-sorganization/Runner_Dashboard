# Code Request Schema & Front-Matter Contract

**Specification Version:** 1.0.0 (CR-2, Issue #1282)  
**Parent Epic:** #1279

## 1. Overview

Each Code Request is represented canonically by a GitHub issue in the target repository. The issue carries the label `code-request` and a status label `code-request:<state>`.

Persistence uses a fenced YAML front-matter block at the very top of the issue body, followed by the user prompt under a standard markdown heading (`## Prompt`).

## 2. Issue Body Format

````markdown
```yaml
id: cr-<repo>-<issue_number>
repository: <repo_name>
state: <state>
board_route: auto | force_board | skip_board
board_proposal: <url_or_ref_or_null>
plan_epic: <url_or_ref_or_null>
planner_profile_id: <profile_id_or_null>
executor_profile_id: <profile_id_or_null>
requester:
  id: <principal_id>
  kind: human | agent
standards:
  - tdd
  - dbc
branch: main
created_at: "<iso8601_utc>"
updated_at: "<iso8601_utc>"
```
````

## Prompt

<prompt text>
```

## 3. Field Definitions

| Field                 | Type             | Required | Description                                                                    |
| --------------------- | ---------------- | -------- | ------------------------------------------------------------------------------ |
| `id`                  | `string`         | Yes      | Unique identifier (conventionally `cr-<repo>-<number>`).                       |
| `repository`          | `string`         | Yes      | Target repository name (e.g. `Runner_Dashboard`).                              |
| `state`               | `enum`           | Yes      | Current lifecycle state.                                                       |
| `board_route`         | `enum`           | Yes      | `auto`, `force_board`, or `skip_board`.                                        |
| `board_proposal`      | `string \| null` | Optional | Link/reference to Architecture Board proposal issue if routed.                 |
| `plan_epic`           | `string \| null` | Optional | Link/reference to tracking epic once technical planning finishes.              |
| `planner_profile_id`  | `string \| null` | Optional | Identifier of planning prompt profile / model.                                 |
| `executor_profile_id` | `string \| null` | Optional | Identifier of execution agent profile / model.                                 |
| `requester.id`        | `string`         | Yes      | Principal ID of the creator (user or agent).                                   |
| `requester.kind`      | `enum`           | Yes      | `human` or `agent`.                                                            |
| `standards`           | `list[string]`   | Yes      | Active engineering standards (`tdd`, `dbc`, `dry`, `lod`, `security`, `docs`). |
| `branch`              | `string`         | Yes      | Target branch (default `main`).                                                |
| `created_at`          | `string`         | Yes      | ISO 8601 UTC timestamp of creation.                                            |
| `updated_at`          | `string`         | Yes      | ISO 8601 UTC timestamp of last update.                                         |

## 4. Lifecycle States

```
draft → triage → {board_review | planning}
board_review → {planning (board:accepted) | deferred | declined}
planning → planned → executing → {done | failed}
any pre-planning state --operator override--> planning | board_review
any state → cancelled (operator)
```

- `draft`: Request created by requester, not yet submitted for triage.
- `triage`: Submitted and queued for triage / automated feasibility analysis.
- `board_review`: Routing requires Architecture Board deliberation.
- `planning`: Fast-tracked or board-accepted; planning engine generates epic/breakdown.
- `planned`: Plan and issue breakdown finalized; ready for execution.
- `executing`: Under active execution by assigned runner / agent.
- `done`: Code generated, tests passing, PR merged, and issue closed.
- `failed`: Execution failed (e.g. tests failing or timeout).
- `deferred`: Board or operator postponed implementation.
- `declined`: Request rejected as out of charter scope.
- `cancelled`: Operator cancelled request.

## 5. Audit Event Comment Format

Whenever a transition occurs, an issue comment is posted:

```markdown
**[Code Request]** State changed from `<from_state>` to `<to_state>` by `<actor>`.

**Reason:** <reason text>
```

For operator overrides, `(override)` is appended to the header line:

```markdown
**[Code Request]** State changed from `draft` to `planning` by `dieterolson`. (override)

**Reason:** Fast-tracking urgent fix
```
