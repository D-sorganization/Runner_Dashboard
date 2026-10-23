import json
from pathlib import Path
from typing import Any

import fleetctl
import pytest
from fleet_client import FleetAPIError, FleetArgumentError, FleetClient
from fleet_tools import BY_CLI


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> FleetClient:
    instance = FleetClient("http://127.0.0.1:9999", token="svc_test_tok")
    monkeypatch.setattr(fleetctl, "FleetClient", lambda *args, **kwargs: instance)
    monkeypatch.delenv("STAFF_RUN_ID", raising=False)
    return instance


@pytest.mark.parametrize("verified", [True, False])
def test_file_verification_posts_exact_content_and_sets_exit_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    client: FleetClient,
    verified: bool,
) -> None:
    path = tmp_path / "document.md"
    content = "# Claim\n\nCitation evidence in Unicode: λ.\n"
    path.write_text(content, encoding="utf-8")
    calls: list[dict[str, Any]] = []

    def request(method: str, route: str, **kwargs: Any) -> dict[str, Any]:
        calls.append({"method": method, "route": route, **kwargs})
        return {"verified": verified, "citations": [], "links": []}

    monkeypatch.setattr(client, "request", request)
    assert fleetctl.main(["library", "verify", "--file", str(path), "--run-id", "run-123"]) == (0 if verified else 1)
    assert json.loads(capsys.readouterr().out)["verified"] is verified
    assert calls == [
        {"method": "POST", "route": "/api/v1/library/verify", "body": {"content": content, "run_id": "run-123"}}
    ]


@pytest.mark.parametrize("kind", ["missing", "binary", "oversized"])
def test_invalid_file_never_sends_a_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    client: FleetClient,
    kind: str,
) -> None:
    path = tmp_path / "document.md"
    if kind == "binary":
        path.write_bytes(b"\xff\xfe")
    elif kind == "oversized":
        path.write_text("x" * 1_000_001, encoding="utf-8")
    monkeypatch.setattr(client, "request", lambda *args, **kwargs: pytest.fail("unexpected network request"))

    assert fleetctl.main(["library", "verify", "--file", str(path)]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_arguments"


def test_api_error_preserves_failure_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    client: FleetClient,
) -> None:
    path = tmp_path / "document.md"
    path.write_text("Claim", encoding="utf-8")

    def request(*args: Any, **kwargs: Any) -> Any:
        raise FleetAPIError(403, {"detail": "run belongs to another principal"})

    monkeypatch.setattr(client, "request", request)
    assert fleetctl.main(["library", "verify", "--file", str(path), "--run-id", "run-other"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == 403


def test_shared_command_uses_content_not_local_file_access(
    client: FleetClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(client, "request", lambda *args, **kwargs: calls.append(kwargs) or {"verified": True})
    monkeypatch.setenv("STAFF_RUN_ID", "run-current")
    command = BY_CLI["library-verify"]
    assert "file" not in command.properties
    command.invoke(client, {"content": "Claim"})
    assert calls == [{"body": {"content": "Claim", "run_id": "run-current"}}]


@pytest.mark.parametrize("arguments", [{"content": "x" * 1_000_001}, {"content": "Claim", "run_id": "bad/run"}])
def test_client_validates_before_network(
    client: FleetClient,
    monkeypatch: pytest.MonkeyPatch,
    arguments: dict[str, Any],
) -> None:
    monkeypatch.setattr(client, "request", lambda *args, **kwargs: pytest.fail("unexpected network request"))
    with pytest.raises(FleetArgumentError):
        client.library_verify(**arguments)


@pytest.mark.parametrize("response", [{}, {"verified": "true"}, None])
def test_invalid_api_response_cannot_report_success(
    client: FleetClient,
    monkeypatch: pytest.MonkeyPatch,
    response: Any,
) -> None:
    monkeypatch.setattr(client, "request", lambda *args, **kwargs: response)
    with pytest.raises(FleetAPIError) as error:
        client.library_verify("Claim")
    assert error.value.status == 502
