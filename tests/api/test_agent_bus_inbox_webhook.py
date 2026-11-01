"""Agent-bus slice 4 (#3682): per-principal inbox webhook fan-out on delivery."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from fastapi.testclient import TestClient
from identity import identity_manager

from tests.api.test_agent_bus_slice1 import _auth, _register

_REMOTE = ("100.64.0.9", 51000)


class _HookHandler(BaseHTTPRequestHandler):
    hits: list[dict[str, Any]] = []
    fail_next = False

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if _HookHandler.fail_next:
            self.send_response(503)
            self.end_headers()
            _HookHandler.fail_next = False
            return
        _HookHandler.hits.append({"path": self.path, "body": json.loads(body.decode())})
        self.send_response(204)
        self.end_headers()


@pytest.fixture
def bus_env(_bus_env_fixture: dict[str, str]) -> dict[str, str]:
    return _bus_env_fixture


@pytest.fixture
def client(bus_env: dict[str, str]) -> TestClient:
    from server import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False, client=_REMOTE)


@pytest.fixture
def hook_server() -> HTTPServer:
    _HookHandler.hits = []
    _HookHandler.fail_next = False
    server = HTTPServer(("127.0.0.1", 0), _HookHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def _configure_webhook(*, role: str, url: str) -> None:
    pid = f"agent-{role}"
    existing = identity_manager.principals[pid]
    identity_manager.principals[pid] = existing.model_copy(update={"inbox_webhook_url": url})


@pytest.mark.unit
def test_send_posts_inbox_webhook_for_role_recipient(
    client: TestClient, bus_env: dict[str, str], hook_server: HTTPServer
) -> None:
    port = hook_server.server_port
    secret = "super-secret-query-token"  # pragma: allowlist secret
    _configure_webhook(role="issue-remediator", url=f"http://127.0.0.1:{port}/hook?token={secret}")
    a = "pr-remediator-webhook-a"
    _register(client, token=bus_env["pr-remediator"], session=a, role="pr-remediator", provider="cursor", node="Hub")
    send = client.post(
        "/api/v1/agent/messages",
        headers=_auth(bus_env["pr-remediator"]),
        json={
            "session": a,
            "to": "issue-remediator",
            "type": "status",
            "repo": "Runner_Dashboard",
            "work_kind": "issue",
            "work_number": 3682,
            "body": "wake off-tailnet",
            "idempotency_key": "webhook-3682-v1",
        },
    )
    assert send.status_code == 200, send.text
    msg = send.json()["message"]
    from agent_bus.inbox_webhook import drain_for_tests

    assert drain_for_tests(timeout=5.0) == 0
    assert len(_HookHandler.hits) == 1
    assert _HookHandler.hits[0]["body"]["event"] == "inbox_message"
    assert _HookHandler.hits[0]["body"]["message"]["id"] == msg["id"]


@pytest.mark.unit
def test_replayed_send_does_not_repost_webhook(
    client: TestClient, bus_env: dict[str, str], hook_server: HTTPServer
) -> None:
    port = hook_server.server_port
    _configure_webhook(role="issue-remediator", url=f"http://127.0.0.1:{port}/hook")
    a = "pr-remediator-webhook-replay"
    _register(client, token=bus_env["pr-remediator"], session=a, role="pr-remediator", provider="cursor", node="Hub")
    payload = {
        "session": a,
        "to": "issue-remediator",
        "type": "status",
        "repo": "Runner_Dashboard",
        "work_kind": "issue",
        "work_number": 3682,
        "body": "once",
        "idempotency_key": "webhook-replay-key",
    }
    first = client.post(
        "/api/v1/agent/messages",
        headers=_auth(bus_env["pr-remediator"]),
        json=payload,
    )
    assert first.status_code == 200
    from agent_bus.inbox_webhook import drain_for_tests

    drain_for_tests(timeout=5.0)
    assert len(_HookHandler.hits) == 1
    replay = client.post("/api/v1/agent/messages", headers=_auth(bus_env["pr-remediator"]), json=payload)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    drain_for_tests(timeout=2.0)
    assert len(_HookHandler.hits) == 1


@pytest.mark.unit
def test_webhook_failure_does_not_block_send(
    client: TestClient, bus_env: dict[str, str], hook_server: HTTPServer
) -> None:
    port = hook_server.server_port
    _HookHandler.fail_next = True
    _configure_webhook(role="issue-remediator", url=f"http://127.0.0.1:{port}/hook")
    a = "pr-remediator-webhook-fail"
    _register(client, token=bus_env["pr-remediator"], session=a, role="pr-remediator", provider="cursor", node="Hub")
    send = client.post(
        "/api/v1/agent/messages",
        headers=_auth(bus_env["pr-remediator"]),
        json={
            "session": a,
            "to": "issue-remediator",
            "type": "status",
            "repo": "Runner_Dashboard",
            "work_kind": "issue",
            "work_number": 3682,
            "body": "still ok",
            "idempotency_key": "webhook-fail-key",
        },
    )
    assert send.status_code == 200, send.text
    from agent_bus.inbox_webhook import drain_for_tests

    drain_for_tests(timeout=5.0)


@pytest.mark.unit
def test_webhook_logs_redact_url_secrets(
    client: TestClient,
    bus_env: dict[str, str],
    hook_server: HTTPServer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    caplog.set_level(logging.INFO, logger="dashboard.agent_bus.inbox_webhook")
    port = hook_server.server_port
    secret = "must-not-appear-in-logs"  # pragma: allowlist secret
    _configure_webhook(role="issue-remediator", url=f"http://127.0.0.1:{port}/hook?token={secret}")
    a = "pr-remediator-webhook-log"
    _register(client, token=bus_env["pr-remediator"], session=a, role="pr-remediator", provider="cursor", node="Hub")
    client.post(
        "/api/v1/agent/messages",
        headers=_auth(bus_env["pr-remediator"]),
        json={
            "session": a,
            "to": "issue-remediator",
            "type": "status",
            "repo": "Runner_Dashboard",
            "work_kind": "issue",
            "work_number": 3682,
            "body": "log check",
            "idempotency_key": "webhook-log-key",
        },
    )
    from agent_bus.inbox_webhook import drain_for_tests

    drain_for_tests(timeout=5.0)
    joined = caplog.text
    assert secret not in joined
