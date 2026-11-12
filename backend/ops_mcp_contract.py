"""Fleet Agent API Standard v1 — ops MCP conformance helpers (Runner_Dashboard #4305).

Subset checked in ``tests/test_ops_mcp_conformance.py``: bounded ``tools/list``, structured
tool errors, ``contract_version`` on tool payloads, and stdio handshake (JSON-RPC subprocess
plus optional real ``mcp`` SDK session). ``run_contract`` is N/A for RD (no model execution).
"""

from __future__ import annotations

from typing import Any, Final

CONTRACT_VERSION: Final = "runner-dashboard-ops-mcp/1.0.0"
MAX_TOOLS_LIST_ITEMS: Final = 256
STRUCTURED_ERROR_KEYS: Final = frozenset({"error", "code", "message", "retryable"})
NA_CONFORMANCE_CHECKS: Final = frozenset({"run_contract"})

OPS_MCP_STDIO_SERVERS: Final = (
    {"id": "fleet", "module_relpath": "clients/fleet/fleet_mcp.py", "mcp_json_key": "fleet"},
    {"id": "rd-threads", "module_relpath": "scripts/rd_threads_mcp.py", "mcp_json_key": "rd-threads"},
    {"id": "scan_sandbox", "module_relpath": "backend/vuln_scan/scan_mcp.py", "mcp_json_key": None},
)

MCP_JSON_SERVER_KEYS: Final = frozenset(s["mcp_json_key"] for s in OPS_MCP_STDIO_SERVERS if s["mcp_json_key"])


def stamp_tool_payload(payload: Any, *, is_error: bool) -> Any:
    """Attach contract metadata to JSON tool payloads returned over MCP stdout."""
    if not isinstance(payload, dict):
        return payload
    stamped = dict(payload)
    stamped.setdefault("contract_version", CONTRACT_VERSION)
    if is_error or stamped.get("ok") is False:
        stamped.setdefault("code", stamped.get("error", "error"))
        if not isinstance(stamped.get("message"), str):
            stamped["message"] = str(stamped.get("message") or stamped.get("code") or stamped.get("error"))
        stamped.setdefault("retryable", bool(stamped.get("retryable", False)))
    return stamped


def structured_error(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a structured tool error envelope (for sandbox / hand-rolled servers)."""
    message = payload.get("message")
    if not isinstance(message, str):
        message = str(payload.get("error", "error"))
    code = payload.get("code")
    if not isinstance(code, str):
        code = str(payload.get("error", "error"))
    return stamp_tool_payload(
        {
            "error": payload.get("error", code),
            "code": code,
            "message": message,
            "retryable": bool(payload.get("retryable", False)),
            **{k: v for k, v in payload.items() if k not in {"error", "code", "message", "retryable"}},
        },
        is_error=True,
    )
