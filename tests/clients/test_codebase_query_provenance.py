"""Codebase client boundaries preserve the server's retrieval evidence."""

from __future__ import annotations

from typing import Any

import fleet_tools
import fleetctl
import pytest
from fleet_client import FleetAPIError, FleetArgumentError, FleetClient

pytestmark = pytest.mark.unit


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> FleetClient:
    monkeypatch.delenv("STAFF_RUN_ID", raising=False)
    return FleetClient("http://127.0.0.1:9999", token="test")


def test_codebase_preserves_bounded_provenance(client: FleetClient, monkeypatch: pytest.MonkeyPatch) -> None:
    citation = "[ref:hub:codebase:Runner_Dashboard@11111111:SPEC.md#L3-L4]"
    passage = {
        "repo": "Runner_Dashboard",
        "source": "SPEC.md",
        "anchor": "L3-L4",
        "text": "界🙂" * 200,
        "citation": citation,
        "commit": "1" * 40,
        "content_hash": "a" * 64,
        "authority": "canonical",
        "authority_rank": 1,
        "status": "active",
        "score": 0.75,
    }
    monkeypatch.setattr(client, "request", lambda *args, **kwargs: {"passages": [passage], "stale": False})
    item = client.fleet_query(source="codebase", query="contracts")["results"][0]
    assert len(item["snippet"].encode("utf-8")) <= 400
    assert item["snippet"] and passage["text"].startswith(item["snippet"])
    assert item["id"] == citation
    for key in ("repo", "source", "anchor", "commit", "content_hash", "authority", "authority_rank", "status", "score"):
        assert item[key] == passage[key]


@pytest.mark.parametrize(
    "explicit,environment,expected",
    [("run_explicit", "run_env", "run_explicit"), (None, "run_env", "run_env"), (None, None, None)],
)
def test_codebase_run_identity_matches_api_query_contract(
    client: FleetClient,
    monkeypatch: pytest.MonkeyPatch,
    explicit: str | None,
    environment: str | None,
    expected: str | None,
) -> None:
    if environment is not None:
        monkeypatch.setenv("STAFF_RUN_ID", environment)
    calls: list[dict[str, Any]] = []

    def request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        assert method == "GET" and path == "/api/v1/staff/knowledge/cartographer/search"
        calls.append(kwargs)
        return {"passages": [], "run_id": expected}

    monkeypatch.setattr(client, "request", request)
    result = client.fleet_query(source="codebase", query="contracts", run_id=explicit)
    assert calls[0]["params"].get("run_id") == expected
    assert ("run_id" in calls[0]["params"]) == (expected is not None)
    assert result.get("run_id") == expected


@pytest.mark.parametrize("identity", ["", "bad\nidentity", "x" * 121])
def test_codebase_invalid_run_identity_fails_before_http(
    client: FleetClient,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
) -> None:
    monkeypatch.setattr(client, "request", lambda *a, **k: pytest.fail("invalid identity reached HTTP"))
    with pytest.raises(FleetArgumentError):
        client.fleet_query(source="codebase", query="contracts", run_id=identity)


def test_codebase_unauthorized_run_does_not_retry_unrecorded(
    client: FleetClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def request(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        raise FleetAPIError(403, {"detail": "not run owner"})

    monkeypatch.setattr(client, "request", request)
    with pytest.raises(FleetAPIError) as error:
        client.fleet_query(source="codebase", query="contracts", run_id="run_owned_elsewhere")
    assert error.value.status == 403 and calls == 1


def test_codebase_cli_uses_shared_command_and_client(client: FleetClient, monkeypatch: pytest.MonkeyPatch) -> None:
    command = fleet_tools.BY_CLI["query-codebase"]
    assert "run_id" in command.properties
    assert "query_codebase" not in fleet_tools.BY_TOOL  # Keep one unified MCP query tool.
    parsed = fleetctl.build_parser().parse_args(["query-codebase", "--query", "contracts", "--run-id", "run_cli"])
    assert parsed.run_id == "run_cli"
    calls = []
    monkeypatch.setattr(client, "request", lambda *a, **k: calls.append(k) or {"passages": []})
    client.query_codebase(query="contracts", run_id="run_cli")
    assert calls[0]["params"]["run_id"] == "run_cli"
