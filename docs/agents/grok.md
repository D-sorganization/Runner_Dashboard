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

### Remote Ingress & Connector Path (SC-F6)

Direct connections use the local network or Tailscale address (`http://deskcomputer:8321`). For cloud-hosted Grok instances outside the private network, remote ingress will route through the authenticated Funnel connector path (tracked in SC-F6, Issue #1335). The API request and response envelopes remain identical.

---

## 2. Authentication & Environment

Set environment variables in Grok's execution context:

```bash
export FLEET_API_URL="http://deskcomputer:8321"
export FLEET_API_TOKEN="$(cat ~/.config/runner-dashboard/agent-tokens/agent-grok.token)"
export FLEET_AGENT="grok"
```

A principal `agent-grok` must be registered in `principals.yml` with `roles: [bot]`. The bot token file is `~/.config/runner-dashboard/agent-tokens/agent-grok.token` (never include token contents in commits or logs).

### Go-live scope (owner decision, 2026-09-26)

The first live contract is the `/api/v1/staff` threads API with the `agent-grok` bearer token above:

- **Reads:** briefing, threads, work items, staff summary and priorities.
- **Dispatch:** only after one supervised dry run (`POST /api/staff/barb/run` with `dry_run: true`, reviewed by the operator). Every later dispatch goes through Barb.
- **Not in scope:** directive writes and hold/unhold writes. These wait for a later owner decision.

---

## 3. Direct `curl` Recipes against `/api/v1/staff`

All POST requests to `/api/v1/staff` require:

- `Authorization: Bearer $FLEET_API_TOKEN`
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
