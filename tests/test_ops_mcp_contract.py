"""Unit tests for backend/ops_mcp_contract.py (#4305 coverage invariant)."""

from __future__ import annotations

from ops_mcp_contract import CONTRACT_VERSION, MCP_JSON_SERVER_KEYS, stamp_tool_payload, structured_error


def test_contract_version_is_stable() -> None:
    assert CONTRACT_VERSION == "runner-dashboard-ops-mcp/1.0.0"


def test_mcp_json_keys_match_ops_servers() -> None:
    assert MCP_JSON_SERVER_KEYS == frozenset({"fleet", "rd-threads"})


def test_structured_error_shape() -> None:
    payload = structured_error({"error": "sandbox_error", "message": "bad argv"})
    assert payload["code"] == "sandbox_error"
    assert payload["message"] == "bad argv"
    assert payload["contract_version"] == CONTRACT_VERSION


def test_stamp_tool_payload_success() -> None:
    stamped = stamp_tool_payload({"ok": True}, is_error=False)
    assert stamped["contract_version"] == CONTRACT_VERSION
