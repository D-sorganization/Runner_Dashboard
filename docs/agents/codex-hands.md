# Codex hands (computer control)

**Dots** is the Codex-based hands agent: it executes concrete work (UI automation, repo operations via staff dispatch) and reports back through staff threads and Barb turnover. Tone matches Barb's stern-dry house style; name and voice stay distinct from Barb.

Principal: **`agent-codex-hands`**

## Scopes

- `fleet.read` — priorities, coordination reads, fleet status
- `staff.dispatch` — `POST /api/staff/{role}/run` (including `dry_run`)
- `staff.read` — Barb continuity reads; `memory.read` / `memory.write` remain on the principal for legacy lesson/task APIs only (not session startup)
- `staff.read`, `priorities.write` (directives), `staff.holds.write` (holds; change only when Dieter approves, then apply directly via `PUT /api/staff/holds`)
- **Not granted:** admin or token-issuing scopes

## Token install

On the machine where Codex runs with computer control:

```bash
export FLEET_API_URL="http://hub-a:8321"
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

## Session startup

Shared-memory context-pack reads are retired for session startup (owner decision 2026-11-12). Read continuity,
priorities, and holds instead:

```bash
curl -s -H "Authorization: Bearer $FLEET_API_TOKEN" \
  "$FLEET_API_URL/api/v1/staff/barb/continuity"

curl -s -H "Authorization: Bearer $FLEET_API_TOKEN" \
  "$FLEET_API_URL/api/priorities"

curl -s -H "Authorization: Bearer $FLEET_API_TOKEN" \
  "$FLEET_API_URL/api/v1/staff/holds"
```

Post status to a staff thread or log Barb turnover when handing off scoped work (see [grok.md](grok.md)).

## Routing Dieter-facing work

Do **not** DM or notify Dieter directly. Open a **staff thread** with Barb (and turnover when on a Barb lease); Barb remains the front door for owner notifications.
