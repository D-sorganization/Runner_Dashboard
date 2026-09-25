# Grok Bot Connection Guide

This guide details how **Grok Bot** connects to the Runner Dashboard staff hub via local execution (`curl` and `fleetctl.py`), interacts with active staff roles (**Barb** and **Orchestrator**), and prepares for the remote connector path.

---

## 1. Overview & Execution Model

Grok Bot executes tools in environments where a resident MCP host may not be available. Grok uses its **local execution capability** to invoke:

1. Direct HTTP `curl` commands against `/api/v1/staff` and `/api/coordination`.
2. The `fleetctl.py` CLI utility (`clients/fleet/fleetctl.py`).

### Active Grok Agents in the Fleet

In the fleet topology, two Grok agent configurations stay active:

- **Barb (Intake & Routing):** Handles work requests, status inquiries, role auto-selection, and conversational routing.
- **Orchestrator (Execution & Scheduling):** Coordinates multi-step runs, monitors holds and schedules, and executes tasks across fleet nodes.

### Remote Ingress & Connector Path (SC-F6)

Direct connections use the local network or Tailscale address (`http://deskcomputer:8321`). For cloud-hosted Grok instances outside the private network, remote ingress will route through the authenticated Funnel connector path (tracked in SC-F6, Issue #1335). The API request and response envelopes remain identical.

---

## 2. Authentication & Environment

Set environment variables in Grok's execution context:

```bash
export FLEET_API_URL="http://deskcomputer:8321"
export FLEET_API_TOKEN="svc_YOUR_TOKEN_HERE"
export FLEET_AGENT="grok"
```

A principal `agent-grok` must be registered in `principals.yml` with `roles: [bot]`.

---

## 3. Direct `curl` Recipes against `/api/v1/staff`

All POST requests to `/api/v1/staff` require:

- `Authorization: Bearer $FLEET_API_TOKEN`
- `X-Requested-With: XMLHttpRequest` (CSRF sentinel)
- `Content-Type: application/json`
- `Idempotency-Key: <unique-key>` (for mutating POST requests)

### Recipe A: Open a Conversation Thread with Barb

```bash
curl -s -X POST "$FLEET_API_URL/api/v1/staff/threads" \
  -H "Authorization: Bearer $FLEET_API_TOKEN" \
  -H "X-Requested-With: XMLHttpRequest" \
  -H "Idempotency-Key: grok-thread-$(date +%s)" \
  -H "Content-Type: application/json" \
  -d '{
    "participant_roles": ["barb"],
    "title": "Grok Triage Inquiry",
    "initial_message": "Hello Barb, what is the status of active runs?"
  }'
```

Response:

```json
{
  "thread_id": "thr_01HZX89AB...",
  "status": "active",
  "created_at": "2026-09-25T07:22:00Z"
}
```

### Recipe B: Send an Idempotent Follow-Up Message

```bash
THREAD_ID="thr_01HZX89AB..."

curl -s -X POST "$FLEET_API_URL/api/v1/staff/threads/$THREAD_ID/messages" \
  -H "Authorization: Bearer $FLEET_API_TOKEN" \
  -H "X-Requested-With: XMLHttpRequest" \
  -H "Idempotency-Key: grok-msg-$(date +%s)" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Please route this task to the issue remediator."
  }'
```

### Recipe C: Read Thread Messages & Poll for Replies

```bash
# Read messages since sequence 0 (or cursor)
curl -s -H "Authorization: Bearer $FLEET_API_TOKEN" \
  "$FLEET_API_URL/api/v1/staff/threads/$THREAD_ID?since_seq=0&limit=20"
```

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
python3 clients/fleet/fleetctl.py staff-thread-open --role barb \
  --message "Grok checking in on fleet health"

# 3. Read thread updates
python3 clients/fleet/fleetctl.py staff-thread-read --thread-id <thread_id> --since-seq 0

# 4. Cancel a stalled run
python3 clients/fleet/fleetctl.py staff-run-cancel --run-id <run_id> --reason "Operator override"
```

---

## 5. Verification Checklist

To verify Grok's connection from a clean shell:

1. Run Recipe A to open a thread with Barb.
2. Confirm HTTP status is `200` or `201`.
3. Read the thread with Recipe C and confirm Barb's automated intake response is returned.
