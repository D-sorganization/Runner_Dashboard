# 0006. Staff conversation model: threads, chat turns vs work runs, session resume, node-local state

## Status

Accepted (retroactive, as built). SC-B2 (#1305), SC-B4 (#1307), SC-B5 (#1308),
SC-B6 (#1313) and SC-B7 (#1314) shipped before this record was written, so it
documents the model as it exists on `main` and lists where the code falls short
of it. The gaps are tracked as follow-up issues #1484–#1494. Governing issue:
SC-B1 (#1299). This record supersedes `docs/assistant-chat-endpoint-design.md`
(#88) and `docs/assistant-agent-mode-design.md` (#89).

Panel review is still required by #1299. Decisions marked **Open** below
belong to the owner.

## Context

Staff runs used to be one-shot CLI subprocesses: a role got a prompt, ran in a
worktree and exited. Nothing modelled a conversation, so every Staff Console
issue (roster, thread view, approvals, run cards, Board meetings) needed a
shared answer to five questions:

1. What are the entities, and how do they relate to runs?
2. When does talking to a role become doing work?
3. How is a provider's context kept between turns?
4. Which node owns conversation state?
5. What happens on failure, and what is private?

## Decision

### 1. Entities (one node-local SQLite file)

Every staff store shares one file: `STAFF_RUNS_DB`, default
`<config dir>/staff_runs.sqlite3` (`backend/staff/store.py`). Each store opens
its own WAL connection.

| Entity         | Table              | Key fields                                                                                            | Notes                                                                                                                                            |
| -------------- | ------------------ | ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Thread         | `threads`          | `kind` (direct, group, auto), `participants`, `status` (open, archived), `unread_counters`, `meta`    | `meta.provider_sessions` holds provider session ids (§3).                                                                                        |
| Message        | `messages`         | `seq`, `author_kind` (user, role, system), `kind`, `body_md`, `delivery`, `run_id`, `idempotency_key` | `kind`: text, action_proposal, action_result, run_card, handoff, status, error. Unique on `(thread_id, seq)` and `(thread_id, idempotency_key)`. |
| ActionProposal | `action_proposals` | `message_id`, `action`, `params`, `risk`, `state`                                                     | States: proposed → approved/denied → executing → done/failed; also expired (24 h).                                                               |
| Run            | `runs`, `events`   | `status`, `thread_id`, `work_item_id`                                                                 | The existing work-run model, extended with thread and work-item links.                                                                           |
| WorkItem       | `work_items`       | `owner_role`, `state` (8), `links` {runs, issues, prs, code_requests}, `next_check_at`                | A ledger for follow-up; it never dispatches on its own.                                                                                          |

An **action** is not a table. It is a registry entry (`ActionDefinition`,
`backend/staff/actions.py`) that executes against a proposal. Its results come
back to the thread as `action_result` and `run_card` messages.

Conversation tables migrate forward only. Migrations are recorded in
`schema_migrations`, and the file is backed up before each one
(`conversation_migrations.py`, currently v2). If a migration fails, the store
reports `available=False` and the thread routes return 503; the dashboard does
not crash.

### 2. Two execution modes

|               | Chat turn                                               | Work run                                           |
| ------------- | ------------------------------------------------------- | -------------------------------------------------- |
| Purpose       | Answer, plan, propose actions                           | Change code or the fleet                           |
| Workspace     | Temporary directory, deleted afterwards                 | Isolated git worktree                              |
| Lease / token | None                                                    | RM lease ritual + scoped `FLEET_API_TOKEN` (SC-E2) |
| Command       | Provider `chat_argv` (read-only flags, §6)              | Provider `build_command`                           |
| Concurrency   | `STAFF_MAX_CHAT_TURNS` (default 4, 1 reserved for Barb) | `STAFF_MAX_CONCURRENT_RUNS` (default 3) + watchdog |
| Output        | Reply contract (§7)                                     | `STAFF_RESULT:` line + classifier                  |

**A chat turn never starts a work run by itself.** A work run starts from:

- an approved `staff.dispatch` or `staff.review_pr` proposal;
- a direct `POST /api/staff/{role}/run` or `/api/v1/staff/{role}/run`;
- the scheduler;
- an answer to a run's needs-input question (`answer_needs_input`), which
  continues the existing run.

`group` threads are a `kind` value. Today a message gets a reply from exactly
one participant. Seat fan-out for the Board is SC-B9 (#1339).

### 3. Session continuity

Each thread stores provider session ids under `meta.provider_sessions`. A turn
first tries the provider's native resume:

| Provider                          | Resume                  |
| --------------------------------- | ----------------------- |
| claude, claude-ollama             | `--resume <id>`         |
| codex, ollama                     | `exec … --session <id>` |
| antigravity, cursor-agent, gemini | `--resume`              |

If resume fails, the turn falls back to **history replay**. The persona and chat
contract go first, then the newest `text` and `action_result` messages from the
last 50 that fit a 4,000-token budget (estimated at 4 characters per token)
(`chat_history.py`).

A resumed turn sends only the new user text. That is correct because the
provider session already holds the persona. The consequence: if a role's
persona is edited, the change reaches existing sessions only after a resume
failure or a new thread.

### 4. Where state lives: node-local

Threads live on the node that received them. There is **no** conversation hub
and no `STAFF_CONVERSATION_HUB` setting, and the thread bus
(`thread_bus.py`) is per process.

Runs forwarded to a peer are proxied for detail, SSE and cancel
(`remote_runs.py`). An unreachable peer gives the classified status
`node_unreachable` / `failure_class: node_offline`.

**Open:** SC-B1 proposed a single conversation authority node. As built, a
forwarded run's status cards are written on the _executing_ node and never
reach the originating thread. The choice between relaying cards home and a
single hub is #1488.

### 5. Privacy and retention

- `redaction.py` removes GitHub, AWS and `sk-` keys, PEM keys, Bearer tokens
  and private IPv4 ranges from `messages.body_md` on write. Every other stored
  field is unredacted (#1489).
- Retention exists only for the audit log (180 days, gzip archive) and for
  expired idempotency keys. Conversations, proposals, runs and work items have
  no retention and no export. **Open:** the owner decides retention windows
  (#1490). Nothing is ever hard-deleted without owner sign-off.

### 6. Chat turns are read-only (enforcement is partial)

Read-only is enforced with provider flags, not a per-tool allowlist:

- claude uses `--permission-mode default`;
- codex and ollama get `--sandbox read-only`;
- other providers have their bypass flags stripped.

No server-side tool executor exists for staff threads. The role schema's
`chat.tools`, `chat.read_only_tools` and `chat.providers` are declarations;
only `chat.tools` is read, and only to check `submit_proposal` permission.
Known hole: a resumed claude turn carries no `--permission-mode` (#1484).

The legacy `/api/assistant/chat` returns 410 and points to
`/api/v1/staff/threads`. `/api/assistant/tool/execute` still runs allowlisted
assistant tools.

### 7. Reply contract

A reply is Markdown prose, optionally followed by:

- one fenced `staff-actions` JSON array of `{action, params, reason}`;
- a `handoff: <role>` line (stored as message meta; inert today);
- a `question: <text>` line.

The parser (`reply_contract.py`) never throws. It uses only the first actions
block and drops unknown or unauthorised actions with warnings. Work runs keep
`STAFF_RESULT:`.

Each proposal passes an approval gate:

- high, critical and owner-only risks need the owner;
- everything else needs `staff.approve`.

Known gaps:

- The chat vocabulary and the executable registry differ (#1486).
- The proposal API is looser than this gate (#1485).
- Action-driven `staff.dispatch` bypasses the `/run` policy (#1487).

### 8. Failure modes

Shared rule: fail visibly (a classified error, never a silent drop or a false
success), keep the user's input, and audit every state change (SC-A8).

| Failure                    | Behaviour as built                                                                                      | Test                                            | Gap                                                                  |
| -------------------------- | ------------------------------------------------------------------------------------------------------- | ----------------------------------------------- | -------------------------------------------------------------------- |
| Provider down              | Health check; fallback chain claude → codex → claude-ollama → ollama; a degraded turn opens a work item | `tests/unit/test_staff_availability.py`         | —                                                                    |
| Slow provider              | Acknowledgement within 3 s                                                                              | same                                            | —                                                                    |
| Node offline mid-run       | Proxied run shows `node_unreachable`; SSE emits `node_unreachable`                                      | `tests/api/test_staff_thread_runs.py` (proxy)   | No `node_unreachable` test; cards stay on the executing node (#1488) |
| Backend restart, work run  | `reconcile.py` marks active runs `failed/orphaned`, kills PIDs, keeps worktrees with unpushed commits   | `tests/unit/test_staff_reconcile.py`            | —                                                                    |
| Backend restart, chat turn | Reply stays `pending`/`streaming` forever                                                               | none                                            | #1491                                                                |
| Chat capacity exhausted    | Logs a warning and runs anyway                                                                          | none                                            | #1492                                                                |
| Duplicate send             | `Idempotency-Key` required + unique index                                                               | `tests/api/test_staff_threads_api.py`           | —                                                                    |
| SSE disconnect             | Replay from `since_seq` / `Last-Event-ID`; 15 s keep-alive                                              | same                                            | Token events are not replayed; tokens arrive only after exit (#1493) |
| Oversized history          | Replay budget (§3)                                                                                      | `tests/unit/test_staff_chat.py`                 | —                                                                    |
| Agent loop                 | Loop guard, 5 bot turns → 429                                                                           | `tests/api/test_staff_spend_and_rate_limits.py` | —                                                                    |
| Rate / budget              | 30 messages/min, 10 dispatches/h; daily budget posts a system message                                   | same                                            | —                                                                    |
| Store migration fails      | Routes return 503                                                                                       | `tests/unit/test_conversations_store.py`        | —                                                                    |

### 9. HTTP surface

All routes are under `/api/v1/staff` (`server.py`):

- **threads:** `POST/GET /threads`, `GET/PATCH /threads/{id}`,
  `POST /threads/{id}/messages` (202), `GET /threads/{id}/stream` (SSE),
  `POST /threads/{id}/read`, `POST /threads/{id}/runs/{run_id}/answer`
- **actions:** `GET /actions[/{name}]`, `GET/POST /proposals`,
  `GET /proposals/{id}`, `POST /proposals/{id}/decide|execute`
- **work items:** `POST/GET /work-items`, `GET/PATCH /work-items/{id}`
- **routing:** `/routing/decide|handoff|override|feedback|eval`
- **follow-up:** `/followup/sweep|status|digest`
- **inbox:** `/inbox` and `/briefing`
- **runs:** `/{role}/run`, run detail, stream and cancel

## Consequences

- Each Staff Console feature has one model to build on. Chat is cheap and safe
  by construction (no worktree, lease or token). Doing work always passes
  through a proposal, the scheduler or an explicit dispatch, so it is audited.
- Node-local state keeps each node independent, and a single-node install needs
  no hub. The cost is cross-node visibility (#1488). A hub, if chosen, must
  arrive as an amendment to this record.
- Native resume keeps turns fast and cheap. Replay keeps threads usable when a
  provider loses its session, at the price of bounded context.
- The read-only guarantee is only as strong as each provider's flags until
  #1484 lands. Treat chat turns as untrusted until then.
- The two superseded assistant design docs stay in place for history. Their
  headers point here.
