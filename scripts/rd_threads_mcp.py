"""rd-threads stdio MCP server (#3072): exactly send, inbox, peers, ask_barb, each one :func:`barb_cli.dispatch`
call on top of ``clients/fleet/fleet_mcp.py``'s JSON-RPC loop; no messaging logic lives here."""

import sys
from typing import Any

try:
    from scripts import barb_cli  # also puts clients/fleet on sys.path
except ImportError:  # run as a plain script
    import barb_cli  # type: ignore[no-redef]

from fleet_mcp import INVALID_PARAMS, FleetMCPServer, _result, _tool_result  # noqa: E402

TOOLS = {
    "send": ("Post an envelope to an agent's thread, or to='*' for the fleet board.", ["to", "kind", "summary"]),
    "inbox": ("Unread envelopes addressed to you (needs fleet.read).", []),
    "peers": ("Live agent sessions (needs fleet.read).", []),
    "ask_barb": ("Ask @barb; options [{label, recommended?}] with exactly one recommended.", ["summary", "options"]),
}
SERVER_INFO = {"name": "rd-threads", "title": "Runner Dashboard threads (@barb)", "version": "1.0.0"}


class RdThreadsMCP(FleetMCPServer):
    def __init__(self, cli: Any = None) -> None:
        super().__init__()
        self._cli = cli

    def handle(self, message: Any) -> dict[str, Any] | None:
        method = message.get("method") if isinstance(message, dict) else None
        if method not in ("tools/list", "tools/call") or "id" not in message:
            response = super().handle(message)
            if method == "initialize" and response and "result" in response:
                response["result"]["serverInfo"] = SERVER_INFO
            return response
        if method == "tools/list":
            schema = {"type": "object", "additionalProperties": True}
            tools = [
                {"name": n, "description": d, "inputSchema": {**schema, "required": r}} for n, (d, r) in TOOLS.items()
            ]
            return _result(message["id"], {"tools": tools})
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if params.get("name") not in TOOLS:
            return {"jsonrpc": "2.0", "id": message["id"], "error": {"code": INVALID_PARAMS, "message": "unknown tool"}}
        self._cli = self._cli or barb_cli.build_cli()
        out = barb_cli.dispatch(self._cli, params["name"], params.get("arguments") or {})
        return _result(message["id"], _tool_result(out, not out.get("ok", False)))


if __name__ == "__main__":
    RdThreadsMCP().serve(sys.stdin, sys.stdout)
