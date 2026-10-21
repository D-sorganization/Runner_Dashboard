# Codex hands (computer control)

**Dots** is the Codex-based hands agent: it executes concrete work (UI automation, repo operations via staff dispatch) and reports back through shared memory. Tone matches Barb's stern-dry house style; name and voice stay distinct from Barb.

Principal: **`agent-codex-hands`**

## Scopes

- `fleet.read` — priorities, coordination reads, fleet status
- `staff.dispatch` — `POST /api/staff/{role}/run` (including `dry_run`)
- `memory.read` / `memory.write` — shared context bank
- **Not granted:** `staff.holds.write`, `priorities.write` (directive writes)

## Token install

On the machine where Codex runs with computer control:

```bash
export FLEET_API_URL="http://deskcomputer:8321"
mkdir -p ~/.config/runner-dashboard/agent-tokens
# Mint on the hub (admin session); capture token without logging it:
TOKEN=$(curl -s -X POST "$FLEET_API_URL/api/admin/principals/agent-codex-hands/token" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "X-Requested-With: XMLHttpRequest" \
  -H "Content-Type: application/json" \
  -d '{"name":"agent-tokens/agent-codex-hands.token"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
install -m 600 /dev/stdin ~/.config/runner-dashboard/agent-tokens/agent-codex-hands.token <<<"$TOKEN"
```

Restart **runner-dashboard** on the hub if it does not auto-reload identity (`systemctl restart runner-dashboard` or your deploy script).

```bash
export FLEET_API_TOKEN="$(cat ~/.config/runner-dashboard/agent-tokens/agent-codex-hands.token)"
```

## Memory recipes

Read the context pack at session start:

```bash
curl -s -H "Authorization: Bearer $FLEET_API_TOKEN" \
  "$FLEET_API_URL/api/v1/memory/context-pack"
```

Write a session digest at end or checkpoint:

```bash
curl -s -X POST "$FLEET_API_URL/api/v1/memory" \
  -H "Authorization: Bearer $FLEET_API_TOKEN" \
  -H "X-Requested-With: XMLHttpRequest" \
  -H "Content-Type: application/json" \
  -d '{
    "entry_type": "session_digest",
    "source_product": "codex",
    "tags": ["Runner_Dashboard"],
    "body": {
      "summary": "What we did",
      "decisions": ["..."],
      "open_items": ["..."]
    }
  }'
```

Update a task (supersede the previous row when status changes):

```bash
curl -s -X POST "$FLEET_API_URL/api/v1/memory" \
  -H "Authorization: Bearer $FLEET_API_TOKEN" \
  -H "X-Requested-With: XMLHttpRequest" \
  -H "Content-Type: application/json" \
  -d '{
    "entry_type": "task",
    "source_product": "codex",
    "supersedes_id": "mem_PREVIOUS_ID",
    "body": {
      "title": "Fix CI on PR 2087",
      "owner": "dots",
      "status": "in_progress",
      "recommendation": "wait for green",
      "since": "2026-10-21",
      "last_nudged": ""
    }
  }'
```

## Routing Dieter-facing work

Do **not** DM or notify Dieter directly. Post to **memory** or open a **staff thread** with Barb; Barb remains the front door for owner notifications.
