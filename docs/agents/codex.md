# Codex CLI Connection Guide

This guide walks through configuring the **Codex CLI** to interact with the Runner Dashboard staff hub via the fleet MCP server, converse with **Barb**, inspect and track work items, and coordinate with fleet peers.

---

## 1. Overview & Architecture

Codex connects via the standard Model Context Protocol (MCP) server provided by `clients/fleet/fleet_mcp.py`. Codex delegates coordination, thread management, and staff conversation calls to MCP tools declared in `clients/fleet/fleet_tools.py`.

---

## 2. Token Minting (Least-Privilege Scopes)

Codex requires a dedicated bot principal with least-privilege scopes:

| Scope                | Purpose                                              | Required for Codex |
| :------------------- | :--------------------------------------------------- | :----------------: |
| `staff.read`         | Inspect staff roster, roles, threads, and work items |        Yes         |
| `staff.chat`         | Converse with Barb and staff roles                   |        Yes         |
| `staff.dispatch`     | Launch staff role execution runs                     |        Yes         |
| `staff.cancel`       | Terminate in-flight staff runs                       |        Yes         |
| `coordination.write` | Register session presence, claims, and peer messages |        Yes         |
| `staff.approve`      | Approve action proposals                             |      Optional      |

### Register Principal & Mint Token

1. In `principals.yml` on the dashboard server:

   ```yaml
   principals:
     - id: agent-codex
       type: bot
       name: Codex CLI Agent
       roles:
         - bot
   ```

2. Mint a bot token:

   ```bash
   curl -s -X POST http://127.0.0.1:8321/api/admin/principals/agent-codex/token \
     -H "X-Requested-With: XMLHttpRequest" \
     -H "Content-Type: application/json" \
     -d '{"name": "codex laptop token", "expires_in_days": 90}'
   ```

---

## 3. Codex MCP Configuration (`~/.codex/config.toml`)

Edit your Codex configuration file located at `~/.codex/config.toml` (on Windows: `%USERPROFILE%\.codex\config.toml`):

```toml
[mcp_servers.fleet]
command = "python3"
args = ["/path/to/Runner_Dashboard/clients/fleet/fleet_mcp.py"]
env = { FLEET_API_URL = "http://deskcomputer:8321", FLEET_API_TOKEN = "svc_YOUR_TOKEN_HERE", FLEET_AGENT = "codex" }
```

_Notes:_

- On Windows, specify `python` or the full path to `python.exe` in `command`.
- Ensure paths use forward slashes or escaped backslashes in TOML.
- `FLEET_AGENT = "codex"` ensures all derived sessions adhere to the required `codex-` session prefix.

---

## 4. Verification: Talking to Barb

Verify the connection by testing staff interaction in Codex:

1. Launch Codex:

   ```bash
   codex
   ```

2. Ask Codex to test the fleet connection:

   > _"Open a new staff thread with Barb asking for a status check on recent work items."_

3. Codex executes:
   - `staff_thread_open(role="barb", message="Status check on recent work items")`
   - `staff_thread_wait(thread_id=..., timeout=30)`
   - Displays Barb's response and thread details.

---

## 5. Troubleshooting Codex Connections

| Issue                               | Cause                          | Fix                                                                                                     |
| :---------------------------------- | :----------------------------- | :------------------------------------------------------------------------------------------------------ |
| `Fleet API error 401: unauthorized` | Token missing or incorrect     | Verify `FLEET_API_TOKEN` in `~/.codex/config.toml`. Check if token expired.                             |
| `Fleet API error 403: forbidden`    | Principal lacks `staff.chat`   | Ensure principal `agent-codex` has `roles: [bot]` in `principals.yml`.                                  |
| `Fleet API error 409: conflict`     | Idempotency or claim collision | If an issue claim fails with 409, another agent is actively working on it.                              |
| Tool timeout during wait            | Run or reply took > 60s        | Call `staff_thread_read(thread_id=..., since_seq=...)` to check for replies that arrived after timeout. |
