"""The documented nested library CLI uses the existing read-only query client."""

import json
from typing import Any

import fleetctl
import pytest
from fleet_client import FleetClient

pytestmark = pytest.mark.unit
CITE = "[ref:DeskComputer:shared:w1:p3#sha256=1234abcd:p.3-3]"


@pytest.mark.parametrize(
    ("arguments", "path", "params"),
    [
        (["search", "energy contracts", "--k", "2"], "/api/v1/library/search", {"q": "energy contracts", "k": 2}),
        (["fetch", CITE], "/api/v1/library/fetch", {"cite": CITE}),
        (["sources"], "/api/v1/library/sources", None),
    ],
)
def test_nested_commands_reach_read_only_api(
    arguments: list[str],
    path: str,
    params: dict[str, Any] | None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FleetClient("http://127.0.0.1:9999", token="svc_test_tok")
    calls: list[dict[str, Any]] = []

    def request(method: str, request_path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append({"method": method, "path": request_path, **kwargs})
        return {"sources": [], "pooled_sources": [], "passages": [], "cite_key": CITE, "text": "energy"}

    monkeypatch.setattr(client, "request", request)
    monkeypatch.setattr(fleetctl, "FleetClient", lambda *args, **kwargs: client)
    assert fleetctl.main(["library", *arguments]) == 0
    assert json.loads(capsys.readouterr().out)["source"] == "library"
    assert len(calls) == 1 and calls[0]["method"] == "GET" and calls[0]["path"] == path
    assert calls[0].get("params") == params


@pytest.mark.parametrize("operation", ["search", "fetch"])
def test_nested_cli_propagates_owned_run_id(operation: str, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FleetClient("http://127.0.0.1:9999", token="svc_test_tok")
    calls = []

    def request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"passages": [], "cite_key": CITE}

    monkeypatch.setattr(client, "request", request)
    monkeypatch.setattr(fleetctl, "FleetClient", lambda *args, **kwargs: client)
    assert (
        fleetctl.main(["library", operation, "energy" if operation == "search" else CITE, "--run-id", "run-123"]) == 0
    )
    assert calls[0]["headers"] == {"X-Staff-Run-ID": "run-123"}


@pytest.mark.parametrize("arguments", [[], ["delete"], ["search"], ["fetch"], ["sources", "--run-id", "run-123"]])
def test_nested_cli_invalid_arguments_do_not_construct_client(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fleetctl, "FleetClient", lambda *args, **kwargs: pytest.fail("unexpected API client"))
    with pytest.raises(SystemExit) as error:
        fleetctl.main(["library", *arguments])
    assert error.value.code == 2
