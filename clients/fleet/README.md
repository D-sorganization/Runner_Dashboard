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
python3 clients/fleet/fleetctl.py library search "energy transfer" --scope local
python3 clients/fleet/fleetctl.py library fetch '[ref:DeskComputer:shared:w1:p3#sha256=1234abcd:p.3-3]' --run-id run-123
python3 clients/fleet/fleetctl.py library fetch desk-x9-library 42 --page 3
python3 clients/fleet/fleetctl.py library fetch desk-x9-library 42 --page 3 --scope federated --run-id run-123
python3 clients/fleet/fleetctl.py library verify --file docs/report.md --run-id run-123
```

Library search omits scope by default, preserving federated shared-only behavior. Pass
`--scope local` to search private local material, or `--scope federated` explicitly.
Fetch accepts either the canonical citation returned by search or a source id, work id,
and one-based page locator. The citation example is illustrative; use a real search
result. Locator fetch defaults to local scope so a private local source can be read
without contacting peers; add `--scope federated` to search registered peers for shared
sources, or `--node NAME` to select one registered peer. Responses preserve the canonical
citation and digest/node/source metadata, with passage text capped at 400 UTF-8 bytes.
Run IDs must belong to the authenticated caller; when omitted, the client uses
`STAFF_RUN_ID` if set. Source discovery reports local and pooled availability using
existing permissions and does not mutate the registry. Citation fetch continues through
the unified query implementation; locator fetch calls the same bounded, read-only API.
Existing flat commands remain available.

Verification reads a UTF-8 file (up to 1,000,000 characters) and sends its content to
the existing verification API. It prints JSON and exits 0 for verified content,
1 for invalid references or API failures, and 2 for invalid local input. The optional
run ID uses the same ownership checks and `STAFF_RUN_ID` default. The Python client
also exposes `library_verify(content, run_id=None)`; the shared command accepts
content rather than granting a tool access to local files.

For setup in each agent (Claude Code, Codex, Gemini CLI and Grok Bot), token minting and
the recommended agent loop, see [`docs/agents/connect.md`](../../docs/agents/connect.md).

To add an endpoint, add a method to `FleetClient` and one `Command` row to `fleet_tools.py`.
The CLI gets the new subcommand automatically. The endpoint also becomes an MCP tool if
the row sets `tool=`. Tests are in `tests/clients/`.
