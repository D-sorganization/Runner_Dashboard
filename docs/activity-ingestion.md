# Activity ingestion v1

`POST /api/v1/activity/events` requires the authenticated `activity.write`
scope and accepts a JSON array of canonical `ActivityEnvelope` v1 records.
It returns the durable redacted records only after the whole batch commits.
The wire body is limited to 256 KiB and 100 events; an empty or malformed
batch returns 422, while oversized bodies or batches return 413.

Every event's `principal` must equal the authenticated caller's ID. The first
committed event binds its source (source type plus opaque node ID) and session
ID to that principal. Subsequent writes cannot take another principal's source
or session (403), including sessions created through the existing local store
API. Producers must use opaque identifiers without secrets or network addresses.

The hub applies the shared staff redactor recursively to all string metadata,
payload keys and payload values, and stamps `redaction_version: 1`; producer
version claims are ignored. Redaction collisions or invalid resulting envelopes
return 422. Validation errors never reflect the raw body.

New events consume a per-principal rolling 60-second quota of 1,000 events and
1 MiB of serialized redacted metadata. A quota failure returns 429 with
`Retry-After: 60`. Quotas persist across restarts. Identical retries of
`(source, session_id, seq)` return the stored record without consuming quota;
conflicting content or reused event IDs return 409.

Binding checks, deduplication, quota accounting and insertion share one SQLite
immediate transaction. A conflict, authorization failure or quota failure rolls
back every new event and quota charge in that batch. Concurrent instances use
the same database transaction boundary. This endpoint neither dispatches work
nor changes deployment or release state.

## Unconnected-tier check-ins (`POST /api/v1/activity/checkins` and `fleetctl checkin`)

Remote and off-tailnet agents use `POST /api/v1/activity/checkins` with a write-only
`activity.checkin` credential (never combined with `activity.read`, `activity.write`, or chat scopes):

- **Tier rules & strict kinds:** check in on **start** (`started`), **claim** (`claimed`, requires `issue`),
  **push** (`pushed`, requires `branch`), **PR open** (`pr_opened`, requires `pr`), **block** (`blocked`),
  and **finish** (`done`), plus optional `heartbeat`. Every check-in sets `source: "checkin"` and `repo`.
- **Bounded offline spool (`fleetctl checkin`):** allocates an idempotent per-session `seq`, persists pending
  envelopes in `~/.config/runner-dashboard/checkin-spool.json` (max 200 events / 256 KiB), retries with
  exponential backoff, and degrades gracefully (`exit 0`, `spooled: true`) when the hub is unreachable.

## Cursor Desktop and Antigravity (`agy`) Session Capture Feasibility (#2873)

This section records the slice **T6** feasibility assessment (epic `#2856`, panel `th_140466bbe535`) for capturing local session activity and transcripts from Cursor Desktop/IDE and Antigravity (`agy`) without scraping undocumented or private storage formats.

| Application / Mode | Supported Capture Surface | Format Stability | Feasibility & Recommendation |
| --- | --- | --- | --- |
| **Cursor Desktop / IDE** (lifecycle metadata) | `.cursor/hooks.json` (`version: 1`, `hooks.sessionStart`, `hooks.stop`) via `scripts/agent-bus-hook-bridge.py` (`shared_scripts/agent_hook_adapters.py`) | **Stable** (versioned JSON hook contract validated by `validate_cursor_hooks`) | **Feasible (metadata only):** keep using `.cursor/hooks.json` (`sessionStart`/`stop`) plus `fleetctl checkin` for session registration and completion events. |
| **Cursor Desktop / IDE** (live chat/composer transcript) | None (IDE chat history is stored inside internal SQLite/LevelDB workspace state `state.vscdb` with no public or versioned export log) | **Unstable / private** (internal Electron/VS Code storage; subject to silent schema breaks) | **Not feasible:** do **not** scrape `state.vscdb` or internal Electron storage. Rely on lifecycle hooks + `fleetctl checkin` for IDE sessions. |
| **Cursor CLI (`cursor-agent`)** | POSIX PTY stream (`fleet-rec`, slice T2 `#2868`) on tailnet nodes; `fleetctl checkin` (`#2875`) on cloud/unconnected runners | **Stable** (standard stdout/stderr terminal stream and v1 check-in envelope) | **Feasible:** wrap interactive/headless CLI runs with `fleet-rec` on connected nodes and `fleetctl checkin` off-tailnet. |
| **Antigravity (`agy`) / Gemini CLI & IDE** (lifecycle hooks) | `.agents/hooks.json` (`fleet-agent-bus.SessionStart`, `fleet-agent-check.PostToolUse`, `fleet-agent-check.Stop`) and `.gemini/settings.json` (`AfterTool`, `AfterAgent`) | **Stable** (managed and drift-checked by `shared_scripts/agent_hook_adapters.py`) | **Feasible:** emit session-start, tool-step, and stop metadata envelopes through `scripts/agent-bus-hook-bridge.py`. |
| **Antigravity (`agy`)** (local session transcripts) | Per-conversation JSONL logs under `<appDataDir>/brain/<conversation-id>/.system_generated/logs/transcript.jsonl` (compact) and `transcript_full.jsonl` | **Stable** (documented line-delimited JSON objects with `step_index`, `source`, `type`, `status`, `created_at`, `content`, `tool_calls`, `truncated_fields`) | **Feasible (opt-in JSONL tailer):** tail `transcript.jsonl` into `TranscriptChunkStore` using a versioned parser with `CaptureGap` fallback and redaction before spool. |

### Cursor Desktop / IDE

1. **Evidence of supported surfaces:** `shared_scripts/agent_hook_adapters.py` generates and validates `.cursor/hooks.json` (`version: 1`) with `sessionStart` and `stop` command hooks invoking `scripts/agent-bus-hook-bridge.py`. In addition, `.cursor/commands/barb.md` (`scripts/wrappers/cursor_barb_hook.py`) provides explicit `/barb` operator messaging.
2. **Why full IDE transcript tailing is not feasible:** Cursor Desktop persists composer and chat turns inside internal VS Code workspace storage (`workspaceStorage/<id>/state.vscdb`) rather than an append-only, versioned JSONL log. Scraping `state.vscdb` violates the epic `#2856` rule against scraping private formats and risks SQLite locking conflicts with the live IDE.
3. **Recommendation:** Mark Cursor Desktop live transcript tailing as **not feasible** until Cursor exposes an official transcript export or streaming hook payload. Use `.cursor/hooks.json` + `fleetctl checkin` for Cursor IDE sessions and `fleet-rec` PTY capture for `cursor-agent` CLI sessions.

### Antigravity (`agy`) / Gemini CLI & IDE

1. **Evidence of supported surfaces:**
   - **Hook contract:** `.agents/hooks.json` supports `SessionStart`, `PostToolUse`, and `Stop` command hooks; `.gemini/settings.json` supports `AfterTool` and `AfterAgent` (`shared_scripts/agent_hook_adapters.py`).
   - **Append-only JSONL transcript log:** Antigravity writes structured per-step JSONL records to `<appDataDir>/brain/<conversation-id>/.system_generated/logs/transcript.jsonl` (and `transcript_full.jsonl` when `truncated_fields` is present). Each line is a self-contained JSON object recording `step_index`, `source` (`USER_EXPLICIT`, `MODEL`, `SYSTEM`), `type` (`USER_INPUT`, `PLANNER_RESPONSE`), `status`, `created_at`, `content`, and `tool_calls`.
2. **Recommendation:** Capture `agy` lifecycle events via `.agents/hooks.json` (`SessionStart` / `PostToolUse` / `Stop`) and opt-in transcript chunks from `transcript.jsonl` (sharing the checkpointing, `redact_terminal_stream` pre-spool redaction, and `CaptureGap` unknown-version handling from `#2866` / `#2871`).

### Follow-up Adapter Proposals

Per the `#2873` acceptance criteria, follow-up adapter work is proposed **only** where a supported surface exists:

- **Cursor Desktop transcript tailer:** **None proposed** (`not feasible` — no supported public transcript log; private `state.vscdb` scraping is disallowed).
- **Antigravity (`agy`) opt-in `transcript.jsonl` tailer (proposed follow-up):** Add an opt-in per-node `agy` JSONL session tailer targeting `<appDataDir>/brain/*/.system_generated/logs/transcript.jsonl` that reuses the versioned parser, byte-offset checkpointing, `redact_terminal_stream` pre-spool redaction, and `CaptureGap` emission from `#2866` and `#2871`.


