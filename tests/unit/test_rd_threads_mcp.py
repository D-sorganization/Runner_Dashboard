"""rd-threads MCP server (#3072): exactly four tools, each a thin call into barb_cli."""

import io
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from scripts import rd_threads_mcp as mcp

ARGS = {
    "send": {"to": "codex", "kind": "update", "summary": "hi"},
    "inbox": {},
    "peers": {},
    "ask_barb": {
        "summary": "?",
        "options": [{"label": "Yes", "recommended": True}, {"label": "No"}],
        "recommendation_reason": "Checks are green.",
    },
}


def _rpc(server: mcp.RdThreadsMCP, method: str, params: Any = None) -> Any:
    return server.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


def test_exposes_exactly_four_tools_and_names_itself() -> None:
    server, out = mcp.RdThreadsMCP(MagicMock()), io.StringIO()
    assert sorted(t["name"] for t in _rpc(server, "tools/list")["result"]["tools"]) == sorted(ARGS)
    server.serve(io.StringIO('{"jsonrpc":"2.0","id":7,"method":"initialize"}\n'), out)
    assert json.loads(out.getvalue())["result"]["serverInfo"]["name"] == "rd-threads"


@pytest.mark.parametrize("tool", sorted(ARGS))
def test_each_tool_delegates_to_barb_cli(tool: str) -> None:
    cli = MagicMock()
    getattr(cli, tool).return_value = {"ok": True, "verb": tool}
    result = _rpc(mcp.RdThreadsMCP(cli), "tools/call", {"name": tool, "arguments": ARGS[tool]})["result"]
    getattr(cli, tool).assert_called_once_with(**ARGS[tool])
    assert json.loads(result["content"][0]["text"]) == {"ok": True, "verb": tool} and result["isError"] is False


def test_unknown_tool_bad_args_and_failures_are_errors_not_crashes() -> None:
    cli = MagicMock()
    server = mcp.RdThreadsMCP(cli)
    assert _rpc(server, "tools/call", {"name": "dispatch"})["error"]["code"] == mcp.INVALID_PARAMS
    cli.send.side_effect = TypeError("unexpected keyword")
    assert _rpc(server, "tools/call", {"name": "send", "arguments": {"bogus": 1}})["result"]["isError"] is True
    cli.inbox.return_value = {"ok": False, "error": "missing_scope", "required_scope": "fleet.read"}
    assert _rpc(server, "tools/call", {"name": "inbox"})["result"]["isError"] is True
