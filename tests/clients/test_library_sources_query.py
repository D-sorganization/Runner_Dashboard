"""Read-only catalog access through the unified client and CLI."""

from typing import Any

import fleet_tools
import fleetctl
import pytest
from fleet_client import FleetAPIError, FleetArgumentError, FleetClient

pytestmark = pytest.mark.unit


def test_sources_preserves_local_and_pooled_availability(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FleetClient("http://127.0.0.1:9999", token="svc_test_tok")
    calls: list[tuple[str, str, dict[str, Any]]] = []
    catalog = {
        "sources": [{"id": "private-local", "shared": False}],
        "pooled_sources": [{"id": "shared-peer", "node": "Brick", "online": False, "last_seen": "earlier"}],
        "current_node": "DeskComputer",
        "is_hub": False,
    }

    def request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        return catalog

    monkeypatch.setattr(client, "request", request)
    assert client.fleet_query(source="library", op="sources") == {**catalog, "status": "ok", "source": "library"}
    assert calls == [("GET", "/api/v1/library/sources", {})]
    assert catalog.get("status") is None


@pytest.mark.parametrize("source", ["context", "knowledge", "memory", "codebase"])
def test_sources_rejects_other_domains_before_network(source: str, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FleetClient("http://127.0.0.1:9999", token="svc_test_tok")
    monkeypatch.setattr(client, "request", lambda *args, **kwargs: pytest.fail("unexpected network request"))
    with pytest.raises(FleetArgumentError, match="sources is only supported for library"):
        client.fleet_query(source=source, op="sources")


@pytest.mark.parametrize("status", [0, 401, 403, 503])
def test_sources_propagates_auth_and_offline_errors(status: int, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FleetClient("http://127.0.0.1:9999", token="svc_test_tok")

    def request(*args: Any, **kwargs: Any) -> Any:
        raise FleetAPIError(status, {"detail": "Unavailable"})

    monkeypatch.setattr(client, "request", request)
    with pytest.raises(FleetAPIError) as error:
        client.library_sources()
    assert error.value.status == status


def test_sources_cli_and_mcp_share_the_read_only_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "sources" in fleet_tools.BY_TOOL["fleet_query"].input_schema()["properties"]["op"]["enum"]
    assert "fleet_library_sources" not in fleet_tools.BY_TOOL
    args = fleetctl.build_parser().parse_args(["library-sources"])
    client = FleetClient("http://127.0.0.1:9999", token="svc_test_tok")
    calls: list[dict[str, Any]] = []

    def query(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr(client, "fleet_query", query)
    assert fleet_tools.BY_CLI[args.command].invoke(client, {}) == {"status": "ok"}
    assert calls == [{"source": "library", "op": "sources"}]
