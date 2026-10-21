"""Local loopback principals may read fleet routes without gaining auth fallbacks."""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import dashboard_config  # noqa: E402
import identity  # noqa: E402
from identity import Principal, TokenRecord  # noqa: E402

_FLEET_TOKEN = "local-reader-fleet-token"  # pragma: allowlist secret
_READER_TOKEN = "local-reader-service-token"  # pragma: allowlist secret
_LOOPBACK = ("127.0.0.1", 51000)
_REMOTE = ("192.0.2.44", 51000)


@pytest.fixture
def make_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., TestClient]]:
    """Build the real fleet-read routers with their external data seams stubbed."""
    import proxy_utils
    from routers import queue, repos, runners

    monkeypatch.setenv("HUB_FLEET_TOKEN", _FLEET_TOKEN)
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "1")
    monkeypatch.setattr(dashboard_config, "MACHINE_ROLE", "hub")
    monkeypatch.setattr(proxy_utils.dashboard_config, "MACHINE_ROLE", "hub")
    monkeypatch.setattr(runners, "cache_get", lambda *_args: {"total_count": 0, "runners": []})
    monkeypatch.setattr(repos, "_cache_get", lambda *_args: [])

    async def empty_queue() -> dict[str, object]:
        return {"runs": []}

    monkeypatch.setattr(queue, "_queue_impl", empty_queue)

    app = FastAPI()
<<<<<<< HEAD
    app.add_middleware(SessionMiddleware, secret_key="unit-test-session-secret")
=======
    app.add_middleware(SessionMiddleware, secret_key="test-secret"  # pragma: allowlist secret)
>>>>>>> b5bfff26 (fix(ci): allowlist session secret in fleet reader test (main drift))
    app.include_router(runners.router)
    app.include_router(repos.router)
    app.include_router(queue.router)

    def build(address: tuple[str, int] = _LOOPBACK) -> TestClient:
        return TestClient(app, raise_server_exceptions=False, client=address)

    clients: list[TestClient] = []

    def make(address: tuple[str, int] = _LOOPBACK) -> TestClient:
        client = build(address)
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.close()


@pytest.mark.unit
@pytest.mark.parametrize("path", ["/api/runners", "/api/repos", "/api/queue"])
def test_loopback_reader_can_fetch_core_fleet_views(make_client: Callable[..., TestClient], path: str) -> None:
    with make_client() as client:
        response = client.get(path)

    assert response.status_code == 200, response.text


@pytest.mark.unit
def test_loopback_reader_does_not_authorize_fleet_mutation(
    make_client: Callable[..., TestClient],
) -> None:
    with make_client() as client:
        response = client.post("/api/queue/purge-stale", json={"dry_run": True})

    assert response.status_code == 401


@pytest.mark.unit
def test_loopback_reader_does_not_accept_invalid_bearer(
    make_client: Callable[..., TestClient],
) -> None:
    with make_client() as client:
        response = client.get("/api/runners", headers={"Authorization": "Bearer invalid"})

    assert response.status_code == 401


@pytest.mark.unit
def test_loopback_reader_does_not_accept_invalid_session_cookie(
    make_client: Callable[..., TestClient],
) -> None:
    with make_client() as client:
        response = client.get("/api/runners", headers={"Cookie": "dashboard_session=invalid"})

    assert response.status_code == 401


@pytest.mark.unit
def test_loopback_reader_ignores_forwarded_loopback_from_remote_peer(
    make_client: Callable[..., TestClient],
) -> None:
    with make_client(_REMOTE) as client:
        response = client.get(
            "/api/runners",
            headers={"X-Forwarded-For": "127.0.0.1"},
        )

    assert response.status_code == 401


@pytest.mark.unit
def test_loopback_reader_requires_existing_loopback_flag(
    make_client: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    with make_client() as client:
        response = client.get("/api/runners")

    assert response.status_code == 401


@pytest.mark.unit
def test_authenticated_principal_without_fleet_read_stays_forbidden(
    make_client: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    principal = Principal(id="bot-without-fleet-read", type="bot", name="Bot", roles=["bot"])
    _register_token(monkeypatch, principal)
    with make_client() as client:
        response = client.get(
            "/api/runners",
            headers={"Authorization": f"Bearer {_READER_TOKEN}"},
        )

    assert response.status_code == 403
    assert response.json()["detail"]["required_scope"] == "fleet.read"


@pytest.mark.unit
def test_valid_service_principal_with_fleet_read_is_preserved(
    make_client: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    principal = Principal(
        id="fleet-reader",
        type="bot",
        name="Fleet Reader",
        roles=["bot"],
        scopes=["fleet.read"],
    )
    _register_token(monkeypatch, principal)
    with make_client() as client:
        response = client.get(
            "/api/runners",
            headers={"Authorization": f"Bearer {_READER_TOKEN}"},
        )

    assert response.status_code == 200, response.text


def _register_token(monkeypatch: pytest.MonkeyPatch, principal: Principal) -> None:
    digest = hashlib.sha256(_READER_TOKEN.encode()).hexdigest()
    record = TokenRecord(
        token_hash=digest,
        principal_id=principal.id,
        created_at=0.0,
        name="test-reader",
    )
    principals = {**identity.identity_manager.principals, principal.id: principal}
    tokens = [*identity.identity_manager.tokens, record]
    monkeypatch.setattr(identity.identity_manager, "principals", principals)
    monkeypatch.setattr(identity.identity_manager, "tokens", tokens)
