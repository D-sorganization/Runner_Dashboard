# Fleet API Clients

These are stdlib-only clients (Python 3.10 or newer, no dependencies) for the Runner
Dashboard Fleet API: staff, coordination and priorities (epic #1192, #1228).

| File              | What it is                                                                           |
| ----------------- | ------------------------------------------------------------------------------------ |
| `fleet_client.py` | `FleetClient`: one validated method per endpoint; raises `FleetAPIError`.            |
| `fleet_tools.py`  | The command table (method, JSON Schema, CLI name, MCP tool name) shared by both.     |
| `fleetctl.py`     | The CLI. Prints JSON; exits 0 on success, 1 on an API error, 2 on bad arguments.     |
| `fleet_mcp.py`    | The MCP stdio server (JSON-RPC 2.0, protocol `2025-06-18`), with 16 `fleet_*` tools. |

```bash
export FLEET_API_URL=http://deskcomputer:8321 FLEET_API_TOKEN=svc_... FLEET_AGENT=claude
python3 clients/fleet/fleetctl.py briefing --repo Runner_Dashboard
python3 clients/fleet/fleetctl.py dispatch night-watch --repo Runner_Dashboard --prompt "sweep" --dry-run
```

```python
import sys; sys.path.insert(0, "clients/fleet")
from fleet_client import FleetClient, FleetAPIError

client = FleetClient()  # reads the FLEET_* environment variables
try:
    # No session given: derived as <agent>-<host>-<YYYYMMDD>; an explicit one must start with "<agent>-".
    client.claim("Runner_Dashboard", 1228, intent="fleet clients")
except FleetAPIError as exc:
    if exc.status == 409:
        ...  # another agent holds the issue, so pick other work
```

## Read-Only Library Commands

```bash
python3 clients/fleet/fleetctl.py library sources
python3 clients/fleet/fleetctl.py library search "energy transfer" --k 3 --run-id run-123
python3 clients/fleet/fleetctl.py library fetch '[ref:DeskComputer:shared:w1:p3#sha256=1234abcd:p.3-3]' --run-id run-123
```

Fetch accepts the canonical citation returned by search. The example is illustrative;
use a real search result. Search and fetch preserve citation metadata and cap passage
snippets at 400 UTF-8 bytes. Run IDs must belong to the authenticated caller; when
omitted, the client uses `STAFF_RUN_ID` if set. Source discovery reports local and
pooled availability using existing permissions and does not mutate the registry.
These commands share the unified query implementation; existing flat commands remain
available. The source/work/page locator form is tracked separately in #2437.

For setup in each agent (Claude Code, Codex, Gemini CLI and Grok Bot), token minting and
the recommended agent loop, see [`docs/agents/connect.md`](../../docs/agents/connect.md).

To add an endpoint, add a method to `FleetClient` and one `Command` row to `fleet_tools.py`.
The CLI gets the new subcommand automatically. The endpoint also becomes an MCP tool if
the row sets `tool=`. Tests are in `tests/clients/`.
