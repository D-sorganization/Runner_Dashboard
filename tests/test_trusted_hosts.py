from __future__ import annotations

from server import app as dashboard_app
from starlette.applications import Starlette
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from trusted_hosts import trusted_hosts_from_env


def test_trusted_hosts_omit_extra_hosts_without_env() -> None:
    hosts = trusted_hosts_from_env(
        {"DASHBOARD_PUBLIC_ORIGIN": "https://hub.example.test"},
        registry={"machines": []},
    )

    assert "testserver" not in hosts


def test_trusted_hosts_include_extra_hosts_when_configured() -> None:
    hosts = trusted_hosts_from_env(
        {"DASHBOARD_TRUSTED_EXTRA_HOSTS": "testserver,peer.example.test"},
        registry={"machines": []},
    )

    assert {"testserver", "peer.example.test"} <= set(hosts)


def test_trusted_hosts_include_local_and_configured_public_origins() -> None:
    hosts = trusted_hosts_from_env(
        {
            "DASHBOARD_PUBLIC_ORIGIN": "https://hub.example.test",
            "DASHBOARD_OAUTH_ALLOWED_ORIGINS": "https://other.example.test,https://bad.example.test/path",
        },
        registry={"machines": []},
    )

    assert {"localhost", "127.0.0.1", "[::1]", "hub.example.test", "other.example.test"} <= set(hosts)
    assert "bad.example.test" not in hosts


def test_trusted_host_middleware_rejects_unconfigured_host() -> None:
    async def home(_request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/", home)])

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])

    response = TestClient(app).get("/", headers={"host": "evil.example"})

    assert response.status_code == 400


def test_dashboard_rejects_unconfigured_host() -> None:
    response = TestClient(dashboard_app).get("/", headers={"host": "evil.example"})

    assert response.status_code == 400


def test_dashboard_accepts_bracketed_ipv6_loopback_host() -> None:
    response = TestClient(dashboard_app).get("/", headers={"host": "[::1]:8321"})

    assert response.status_code != 400


def test_loopback_request_without_credentials_is_unauthorized(monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_LOOPBACK_AUTH", raising=False)
    client = TestClient(dashboard_app, client=("127.0.0.1", 8321), raise_server_exceptions=False)

    response = client.post(
        "/api/staff/runs/run-x/cancel",
        headers={"host": "localhost:8321", "X-Requested-With": "XMLHttpRequest"},
    )

    assert response.status_code == 401


def test_setup_does_not_seed_loopback_admin_auth() -> None:
    from pathlib import Path

    script = Path("deploy/setup.sh").read_text(encoding="utf-8")
    assert "printf 'DASHBOARD_LOOPBACK_AUTH=1\\n'" not in script
