# Claude Connection Guide (Claude Code & Claude Cowork)

This guide walks through configuring **Claude Code** (CLI) and **Claude Cowork** (Desktop) to communicate with the Runner Dashboard staff hub, converse with **Barb** and other staff roles, dispatch runs, and review work items.

---

## 1. Overview & Architecture

Claude clients interact with the Staff Console through the fleet MCP server (`clients/fleet/fleet_mcp.py`). The MCP server exposes standard JSON-RPC tools that map to `/api/v1/staff` and `/api/coordination` endpoints.

```
+---------------------------+       MCP (JSON-RPC)       +--------------------------+
| Claude Code / Cowork      | <========================> | clients/fleet/fleet_mcp  |
+---------------------------+                            +--------------------------+
                                                                      |
                                                              HTTP / Tailscale
                                                                      v
                                                         +--------------------------+
                                                         | Runner Dashboard (v1)    |
                                                         | /api/v1/staff/*          |
                                                         | /api/coordination/*      |
                                                         +--------------------------+
```

---

## 2. Token Minting (Least-Privilege Scopes)

Each external agent should have a distinct bot principal. Under the SC-F1 identity model, permissions are enforced via explicit scopes:

| Scope                | Purpose                                                    |    Required for Claude    |
| :------------------- | :--------------------------------------------------------- | :-----------------------: |
| `staff.read`         | Read roster, role definitions, threads, and run status     |            Yes            |
| `staff.chat`         | Open threads and send chat turns to Barb and roles         |            Yes            |
| `staff.dispatch`     | Launch staff role execution runs                           |            Yes            |
| `staff.cancel`       | Terminate in-flight staff runs                             |            Yes            |
| `coordination.write` | Register presence, issue claims, send peer messages        |            Yes            |
| `staff.approve`      | Approve or deny action proposals (`staff_approval_decide`) | Optional (operators only) |
| `staff.holds.write`  | Manage operational holds on roles                          |            No             |
| `staff.admin`        | Export usage ledgers or modify billing                     |            No             |

### Step 1: Register the Principal

On the dashboard node, add the principal to `principals.yml` (located in `$DASHBOARD_IDENTITY_DIR`, default `~/.config/runner-dashboard/`):

```yaml
principals:
  - id: agent-claude
    type: bot
    name: Claude Assistant Client
    roles:
      - bot
```

Restart the dashboard service (`sudo systemctl restart runner-dashboard.service`) to load the new principal.

### Step 2: Mint the Service Token

From an authorized operator session or local loopback (`DASHBOARD_LOOPBACK_AUTH=1`):

```bash
curl -s -X POST http://127.0.0.1:8321/api/admin/principals/agent-claude/token \
  -H "X-Requested-With: XMLHttpRequest" \
  -H "Content-Type: application/json" \
  -d '{"name": "claude client token", "expires_in_days": 90}'
```

Save the generated `svc_...` token securely. You will pass it via `FLEET_API_TOKEN`.

---

## 3. Claude Code Setup (CLI)

Add the fleet MCP server using the `claude mcp add` command:

```bash
claude mcp add fleet --scope user \
  --env FLEET_API_URL=http://deskcomputer:8321 \
  --env FLEET_API_TOKEN=svc_YOUR_TOKEN_HERE \
  --env FLEET_AGENT=claude \
  -- python3 /path/to/Runner_Dashboard/clients/fleet/fleet_mcp.py
```

_On Windows, use `python` instead of `python3`._

### Verify MCP Registration

```bash
claude mcp list
```

You should see `fleet` listed with tools such as `mcp__fleet__staff_thread_open`, `mcp__fleet__staff_message_send`, and `mcp__fleet__fleet_briefing`.

---

## 4. Claude Cowork Setup (Desktop)

For Claude Desktop (Cowork), configure the server in `claude_desktop_config.json`:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`

Add the `fleet` entry under `mcpServers`:

```json
{
  "mcpServers": {
    "fleet": {
      "command": "python3",
      "args": ["/path/to/Runner_Dashboard/clients/fleet/fleet_mcp.py"],
      "env": {
        "FLEET_API_URL": "http://deskcomputer:8321",
        "FLEET_API_TOKEN": "svc_YOUR_TOKEN_HERE",
        "FLEET_AGENT": "claude"
      }
    }
  }
}
```

Restart Claude Desktop to apply the configuration.

---

## 5. Verification: Talking to Barb

Verify end-to-end communication by starting a conversation with **Barb** (the organization's intake and routing role):

1. **Open a Thread:**
   In Claude, instruct:

   > _"Call `staff_thread_open` with role `auto` (or `barb`) and message `Hello Barb, what are the top priorities today?`"_

2. **Wait for the Reply:**

   > _"Call `staff_thread_wait` for the new thread ID to wait for Barb's response."_

3. **Inspect the Result:**
   Barb will reply with the organization's current priority backlog and triage state.

---

## 6. Common Troubleshooting

| Symptom                        | Cause                    | Resolution                                                                                                  |
| :----------------------------- | :----------------------- | :---------------------------------------------------------------------------------------------------------- |
| Error `unauthorized` (401)     | Invalid or expired token | Check `FLEET_API_TOKEN` and ensure it starts with `svc_`. Mint a fresh token.                               |
| Error `forbidden` (403)        | Missing required scope   | Ensure `agent-claude` principal in `principals.yml` has the `bot` role or carries `staff.chat`.             |
| Error `unreachable` (status 0) | Network unreachable      | Verify `FLEET_API_URL` is accessible over your Tailscale or local network (`curl "$FLEET_API_URL/health"`). |
| Session rejected               | Session prefix mismatch  | Ensure explicit sessions start with `claude-` (e.g. `claude-workstation-20260925`).                         |
