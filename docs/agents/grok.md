# Grok Bot Connection Guide

This guide details how **Grok Bot** connects to the Runner Dashboard staff hub via local execution (`curl` and `fleetctl.py`), interacts with staff front door **Barb** (the Orchestrator role is retired and folded into Barb per Repository_Management#1733), and prepares for the remote connector path.

---

## 1. Overview & Execution Model

Grok Bot executes tools in environments where a resident MCP host may not be available. Grok uses its **local execution capability** to invoke:

1. Direct HTTP `curl` commands against `/api/v1/staff` and `/api/coordination`.
2. The `fleetctl.py` CLI utility (`clients/fleet/fleetctl.py`).

### Active Grok Agents in the Fleet

In the fleet topology, **Barb** is the single front door:

- **Barb (Front Door, Intake & Routing):** Handles work requests, status inquiries, role auto-selection, and conversational routing.
- _Note:_ The Orchestrator role is retired and folded into Barb (Repository_Management#1733).

### Remote Ingress (ADR 0007: local-only)

Direct connections use the local network or Tailscale address (`http://hub-a:8321`). **Cloud-hosted Grok instances outside the private network are not supported** ([ADR 0007](../adr/0007-agent-client-ingress-local-only.md)): the dashboard has no Funnel, connector or outbound relay path. Grok connects through its local-exec tool on a fleet machine, over the tailnet; that is a local process calling a cloud model, not cloud ingress. See [Supported Clients and Networks](connect.md#supported-clients-and-networks). Any future cloud ingress is a separate Board decision.

---

## Dispatcher-First Protocol

Grok handles conversation, decisions, holds and owner questions itself. Route other
work through RD, subject to the existing proposal-confirmation and cost gates
below. A dispatch refusal never grants permission to bypass a hold.

1. Read the coordination briefing and claim the issue on RM #2539 before starting
   work. Check for an existing run and live RD lease for the same request key.
2. Probe the hub, then dispatch through `fleetctl dispatch` with a stable
   `Idempotency-Key`. Reuse the key on retries: it identifies the role, repository,
   issue, prompt digest and UTC date. Never create a new key just to evade a lease.
   The ordered `FLEET_API_URLS` health-probe/failover client is pending #3051;
   confirm that implementation has landed before using this planned invocation:

   ```bash
   python3 clients/fleet/fleetctl.py dispatch --role ROLE \
     --repo Runner_Dashboard --issue ISSUE --prompt-file PROMPT_FILE
   ```

   Until then, use the approved Barb proposal path below. Do not treat a missing
   CLI subcommand as proof that the hub is unavailable.
3. Wait without polling: use `staff_thread_wait` with a long timeout, or consume
   the terminal run-card when the notifier (#3052) is available. A wait timeout
   alone is not a failed run. Keep stream/transcript reads out of Grok's context.
4. Fall back only for one of these named reasons:

   | Reason | Required evidence |
   | --- | --- |
   | `hub_unreachable` | two probes fail to reach the configured hub service |
   | `run_failed_twice` | the run terminally failed twice; a pending run does not qualify |
   | `no_provider` | RD reports no provider available |
   | `small_task` | the task takes less than about 5 minutes and run-cards are not live |

   Never fall back while a live RD lease for the same key exists. If lease status
   cannot be verified, wait or escalate through Barb. Use a local subscription CLI
   on a fleet machine first (claude, codex, agy or cursor); use Grok itself last.
   A `held` or `rejected` response is a refusal, not a reason to try another hub.
5. Every fallback writes a ledger/check-in record with its reason and task
   identity. Use `fleetctl checkin` (`python3 clients/fleet/fleetctl.py checkin {started,claimed,pushed,pr_opened,blocked,done} --repo <repo>`,
   shared bounded offline spool from #2874/#2875) and the dispatch
   ledger from #3051/#3054 when available; do not build a parallel spool. Link
   the result back to the RD thread when reachable.
6. Relay only the verified outcome and links to Dieter. Check the resulting PR,
   tests or artifact; a terminal success flag alone does not verify the work.

These gates apply to RD dispatch and every fallback:

| Hold | Required behavior |
| --- | --- |
| #2657 | No RD deploy or release while the hold remains active |
| Sidekick packaging | Keep packaging work held |
| Assessment Board | Preserve the Assessment Board gate |
| C3 | Preserve the C3 gate |

Keep Gasification_Model private/work content out of public issues, comments,
digests and Pages. Use authenticated private records for sensitive context; never
include tokens in a ledger, prompt log or error report. Subscription CLIs only;
no pay-per-use model APIs.

## 2. Authentication & Environment

Set environment variables in Grok's execution context:

```bash
export FLEET_API_URL="http://hub-a:8321"
export FLEET_API_TOKEN="$(cat ~/.config/runner-dashboard/agent-tokens/agent-grok.token)"
export FLEET_AGENT="grok"
```

A principal `agent-grok` must exist in the identity store with the `bot` role. On hub
deploys, `config/agent_principals.yml` merges repo defaults at startup: `agent-grok`
gets `roles: [bot]` plus explicit `fleet.read` and `memory.read` / `memory.write`
(shared conversation context) while **not** receiving `priorities.write` or
`staff.holds.write`. Place the bearer in
`~/.config/runner-dashboard/agent-tokens/agent-grok.token`; the dashboard ingests
that file on restart (in addition to `tokens.yml` entries from the admin mint API).
Never commit token contents or logs.

Barb uses **`memory.read`** and **`memory.write`** to share conversation context with hands agents. See the [assistant bridge contract](README.md).

### Shared memory (context pack)

At the start of a session, read the compact context pack (digests, open tasks, directives, holds):

```bash
curl -s -H "Authorization: Bearer $FLEET_API_TOKEN" \
  "$FLEET_API_URL/api/v1/memory/context-pack"
```

At checkpoints and session end, append a `session_digest` (decisions, open items):

```bash
curl -s -X POST "$FLEET_API_URL/api/v1/memory" \
  -H "Authorization: Bearer $FLEET_API_TOKEN" \
  -H "X-Requested-With: XMLHttpRequest" \
  -H "Content-Type: application/json" \
  -d '{
    "entry_type": "session_digest",
    "source_product": "grokbot",
    "tags": ["Runner_Dashboard"],
    "body": {
      "summary": "What Barb and Dieter covered",
      "decisions": [],
      "open_items": []
    }
  }'
```

Hands agents such as [`agent-codex-hands`](codex-hands.md) also write digests and `task` rows when executing work.

### Go-live scope (owner decisions, 2026-09-26 and 2026-09-27)

The first live contract is the `/api/v1/staff` threads API with the `agent-grok` bearer token above:

- **Reads:** briefing, threads, work items, staff summary, priorities and directives
  (`fleet.read`), coordination (`/api/coordination/*`), fleet status, and shared memory
  (`GET /api/v1/memory/context-pack`).
- **Memory writes:** `POST /api/v1/memory` session digests (`memory.write`).
- **Directives and holds (owner decision 2026-10-31):** `priorities.write` and `staff.holds.write`. Write directives when Dieter asks. Holds change only when Dieter approves; then lift or set the hold directly via `PUT /api/staff/holds`.
- **Dispatch: direct** (owner decision 2026-10-31, superseding 2026-09-27 "Allow with confirm"). Barb dispatches
  any staff role with `staff.dispatch`, in parallel and without a proposal card. The cost guard still applies.
  History: the 2026-09-27 confirm gate and its dry runs are below.
  - The supervised dry run gate is met. The first dry run (2026-09-27, hub-a) showed that Barb's run plan
    carried write tools and push/PR rules. That was fixed by the opt-in `permissions.code_read_only` (#1659,
    Repository_Management#1822) and by removing Barb's directive-write recipe (Repository_Management#1818).
  - The re-run on `dfbd139d` passed: a read-only tool allowlist, no draft-PR rules, no directive write, and
    nothing queued.
- **Dispatch (2026-10-31):** Barb principals hold `staff.dispatch` and may call `POST /api/staff/{role}/run` directly when Dieter asks.

---

## 3. Direct `curl` Recipes against `/api/v1/staff`

All POST requests to `/api/v1/staff` require:

- `Authorization: Bearer $FLEET_API_TOKEN` (including `POST …/{role}/run` with `dry_run: true`; loopback without a bearer is rejected, #2053)
- `X-Requested-With: XMLHttpRequest` (CSRF sentinel)
- `Content-Type: application/json`
- `Idempotency-Key: <unique-key>` (for mutating POST requests)

### Recipe A: Open a Conversation Thread with Barb

A thread is opened first, then the first message is posted into it (Recipe B). The body takes
`title`, `kind`, `role` and `participants` only; any other key is rejected with 422.

```bash
THREAD_ID=$(curl -s -X POST "$FLEET_API_URL/api/v1/staff/threads"   -H "Authorization: Bearer $FLEET_API_TOKEN"   -H "X-Requested-With: XMLHttpRequest"   -H "Idempotency-Key: grok-thread-$(date +%s)"   -H "Content-Type: application/json"   -d '{"role": "barb", "title": "Grok Triage Inquiry"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
```

Response (`201`), abridged:

```json
{
  "id": "th_b47ed2c88efd",
  "title": "Grok Triage Inquiry",
  "kind": "direct",
  "participants": ["barb", "agent-grok"],
  "status": "open",
  "created_at": "2026-09-27T03:46:48.490561Z"
}
```

### Recipe B: Send a Message

The body takes `body` (markdown text), and optionally `kind` and `meta`; any other key is rejected with 422.
Reuse the same `Idempotency-Key` when retrying the same message, so a retry never posts it twice.

```bash
curl -s -X POST "$FLEET_API_URL/api/v1/staff/threads/$THREAD_ID/messages"   -H "Authorization: Bearer $FLEET_API_TOKEN"   -H "X-Requested-With: XMLHttpRequest"   -H "Idempotency-Key: grok-msg-$(date +%s)"   -H "Content-Type: application/json"   -d '{"body": "Hello Barb, what is the status of active runs?"}'
```

The response (`202`) holds the stored `message` and a reply placeholder. Barb's answer arrives in the thread,
usually within seconds.

### Recipe C: Read Thread Messages & Poll for Replies

```bash
# Messages after sequence 0; pass the last seq you have seen to read only new ones
curl -s -H "Authorization: Bearer $FLEET_API_TOKEN"   "$FLEET_API_URL/api/v1/staff/threads/$THREAD_ID?since_seq=0"
```

The response is `{"thread": {...}, "messages": [...]}`. Each message has `seq`, `author_kind` (`user`, `role`,
`system`), `author`, `kind` and the text in `body_md`. `fleetctl.py` and the fleet MCP tools
(`clients/fleet/`) wrap Recipes A to C, including the two-step open-then-post.

### Recipe D: Query Active Work Items

```bash
curl -s -H "Authorization: Bearer $FLEET_API_TOKEN" \
  "$FLEET_API_URL/api/v1/staff/work-items?state=in_progress&limit=10"
```

### Recipe E: Run an Expert Panel (#1634)

An expert panel puts 3-4 read-only expert seats on a hard question. They take turns for 1-6 rounds, each
seeing the whole discussion so far, and a `Moderator` then writes the synthesis (consensus, agreed points,
open disagreements, recommendation, confidence). A `debate` stops early when every expert in a round ends
with `STANCE: agree`; a `brainstorm` runs every round and ranks ideas instead.

**When Barb uses it:** only when Dieter asks for a panel, debate or brainstorm in the chat. Propose the
experts (or a preset from `GET /api/v1/staff/panels/presets`), the rounds and the mode first, and start it
once he agrees. A panel spends real model time, so never start one on your own initiative.

```bash
curl -s -X POST "$FLEET_API_URL/api/v1/staff/panels"   -H "Authorization: Bearer $FLEET_API_TOKEN"   -H "X-Requested-With: XMLHttpRequest"   -H "Content-Type: application/json"   -d '{"topic": "<question>", "mode": "debate", "rounds": 3,
       "experts": [{"name": "Architect", "perspective": "Architecture and maintainability"},
                   {"name": "Skeptic", "perspective": "Failure modes and hidden costs"},
                   {"name": "Operator", "perspective": "Operation, cost and monitoring"}]}'
```

- `202` returns `{"thread": {...}, "estimate": {"total_cost_usd": ...}}`. Seats default to `claude`; set
  `"provider"` and `"model"` per expert to mix CLIs (`GET .../panels/presets` lists the allowed providers).
- A `400` with `code: group_cost_guard_threshold_exceeded` means the estimate is over the cost threshold: tell
  Dieter the estimate and re-send with `"confirm_cost": true` only after he agrees.
- Poll `GET /api/v1/staff/panels/$THREAD_ID` until `status` is `complete` or `failed`; it returns `turns`
  (expert, round, stance, position, text), `consensus`, `rounds_used` and `synthesis`. Relay the synthesis,
  not the whole transcript. A panel thread takes no new messages (409); start a new panel to follow up.

---

## 4. CLI Recipes via `fleetctl.py`

When Python 3 is available in Grok's environment, `fleetctl.py` handles session derivation, retries, and formatting:

```bash
# 1. Fetch fleet briefing
python3 clients/fleet/fleetctl.py briefing --repo Runner_Dashboard

# 2. Open thread with Barb
python3 clients/fleet/fleetctl.py thread-open --role barb \
  --initial-message "Grok checking in on fleet health"

# 3. Read thread updates
python3 clients/fleet/fleetctl.py thread-read <thread_id> --since-seq 0

# 4. Cancel a stalled run
python3 clients/fleet/fleetctl.py cancel <run_id>
```

---

## 5. Verification Checklist

To verify Grok's connection from a clean shell:

1. Run Recipe A to open a thread with Barb.
2. Confirm HTTP status is `200` or `201`.
3. Post a message with Recipe B, then read the thread with Recipe C and confirm a message with
   `author_kind: "role"` and `author: "barb"` is returned.
