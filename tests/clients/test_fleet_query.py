"""Tests for RD #2223: Unified fleet_query on-request context tool."""

from __future__ import annotations

from typing import Any

import fleet_tools
import pytest
from fleet_client import FleetAPIError, FleetClient


def test_fleet_query_mcp_tool_registration() -> None:
    assert "fleet_query" in fleet_tools.BY_TOOL
    cmd = fleet_tools.BY_TOOL["fleet_query"]
    assert cmd.tool == "fleet_query"
    schema = cmd.input_schema()
    props = schema["properties"]
    assert set(props["source"]["enum"]) == {"context", "library", "knowledge", "memory"}
    assert "op" in props
    assert props["k"]["maximum"] == 5
    assert schema["required"] == ["source"]
    # Verify legacy individual tools are NOT registered as MCP tools
    assert "fleet_context" not in fleet_tools.BY_TOOL
    assert "fleet_library_search" not in fleet_tools.BY_TOOL
    assert "fleet_knowledge_search" not in fleet_tools.BY_TOOL
    assert "fleet_request_context" not in fleet_tools.BY_TOOL


def test_fleet_query_compact_snippets_and_result_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FleetClient("http://127.0.0.1:9999", token="svc_test_tok")

    long_text = "Important fleet context details. " * 30  # > 400 chars
    fake_items = [
        {"id": f"item-{i}", "title": f"Doc {i}", "text": long_text, "url": f"https://repo/{i}"} for i in range(12)
    ]

    def mock_request(method: str, path: str, **kwargs: Any) -> Any:
        return {"items": fake_items, "total": 12}

    monkeypatch.setattr(client, "request", mock_request)

    # By default k=5, snippets capped <= 400 chars even with full=True
    res = client.fleet_query(source="library", query="context", full=True)
    assert len(res["results"]) <= 5
    for item in res["results"]:
        assert len(item["snippet"]) <= 400
        assert "ref" in item or "url" in item


def test_fleet_query_context_offline_hub_explicit_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FleetClient("http://127.0.0.1:9999", token="svc_test_tok")

    def mock_request_error(method: str, path: str, **kwargs: Any) -> Any:
        raise FleetAPIError("Connection refused", status=0)

    monkeypatch.setattr(client, "request", mock_request_error)

    res = client.fleet_query(source="context", repo="Runner_Dashboard")
    assert res["status"] == "unavailable"
    assert res["holds"] == "unavailable"
    assert res["directives"] == "unavailable"


def test_fleet_query_context_barb_only_exclusion(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FleetClient("http://127.0.0.1:9999", token="svc_agent_tok")

    def mock_request(method: str, path: str, **kwargs: Any) -> Any:
        return {
            "session_digests": [],
            "open_tasks": [],
            "directives": [
                {"id": "d1", "text": "Public rule", "barb_only": False},
                {"id": "d2", "text": "Secret barb directive", "barb_only": True},
            ],
            "holds": ["C3 HOLD"],
        }

    monkeypatch.setattr(client, "request", mock_request)

    res = client.fleet_query(source="context", repo="Runner_Dashboard")
    assert res["status"] == "ok"
    directive_texts = [d["text"] for d in res["directives"]]
    assert "Public rule" in directive_texts
    assert "Secret barb directive" not in directive_texts


def test_fleet_query_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FleetClient("http://127.0.0.1:9999", token="svc_test_tok")

    fake_items = [{"id": f"item-{i}", "title": f"Doc {i}", "text": f"Snippet {i}"} for i in range(10)]

    def mock_request(method: str, path: str, **kwargs: Any) -> Any:
        return {"items": fake_items, "total": 10}

    monkeypatch.setattr(client, "request", mock_request)

    page1 = client.fleet_query(source="memory", k=3, offset=0)
    page2 = client.fleet_query(source="memory", k=3, offset=3)

    assert len(page1["results"]) == 3
    assert len(page2["results"]) == 3
    assert page1["results"][0]["id"] != page2["results"][0]["id"]
    assert page1["has_more"] is True


def test_fleet_query_context_caching(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FleetClient("http://127.0.0.1:9999", token="svc_cache_tok")
    call_count = 0

    def mock_request(method: str, path: str, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        return {"session_digests": [], "open_tasks": [], "directives": [], "holds": []}

    monkeypatch.setattr(client, "request", mock_request)

    # First call hits mock_request
    res1 = client.fleet_query(source="context", repo="Tools")
    assert call_count == 1

    # Second identical query within freshness window hits cache
    res2 = client.fleet_query(source="context", repo="Tools")
    assert call_count == 1
    assert res1 == res2


def test_fleet_query_request_op_routes_to_librarian(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FleetClient("http://127.0.0.1:9999", token="svc_test_tok")
    sent: dict[str, Any] = {}

    def mock_send(recipient: str, body: str, intent: str = "general") -> Any:
        sent["recipient"] = recipient
        sent["body"] = body
        sent["intent"] = intent
        return {"id": "msg-123", "status": "sent"}

    monkeypatch.setattr(client, "send_message", mock_send)

    res = client.fleet_query(source="context", op="request", query="Need guidance on database migrations")
    assert res["status"] == "requested"
    assert sent["recipient"] == "librarian"
    assert "guidance" in sent["body"]


def test_fleetctl_granular_subcommands_exist() -> None:
    assert "query-context" in fleet_tools.BY_CLI
    assert "query-library" in fleet_tools.BY_CLI
    assert "query-knowledge" in fleet_tools.BY_CLI
    assert "query-memory" in fleet_tools.BY_CLI
