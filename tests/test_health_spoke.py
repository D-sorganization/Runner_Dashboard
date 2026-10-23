"""Spoke health uses hub connectivity while retaining this node's identity (#2006)."""

from __future__ import annotations

import asyncio
import secrets
import sys
import time
import types
from unittest.mock import AsyncMock

import dashboard_config
import health
import proxy_utils
import pytest
from fastapi import HTTPException, Request


@pytest.fixture
def spoke(monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncMock, AsyncMock]:
    direct = AsyncMock(side_effect=RuntimeError("satellite has no org runner access"))
    server = types.SimpleNamespace(
        BOOT_TIME=time.time() - 60,
        HOSTNAME="spoke-machine",
        ORG="D-sorganization",
        _cache_get=lambda *_args: None,
        _cache_set=lambda *_args: None,
        _deployment_info=lambda: {"git_sha": "local-deployment"},
        gh_api_admin=direct,
    )
    upstream = AsyncMock(
        return_value={
            "status": "healthy",
            "hostname": "hub-machine",
            "github_api": "connected",
            "github_auth_source": "github_app",
            "github_auth_status": "ok",
            "github_error_type": None,
            "runners_registered": 23,
            "deployment": {"git_sha": "hub-deployment"},
        }
    )
    monkeypatch.setitem(sys.modules, "server", server)
    monkeypatch.setattr(health, "proxy_to_hub", upstream, raising=False)
    monkeypatch.setattr(health, "record_dashboard_health", lambda *_args: None)
    monkeypatch.setattr(dashboard_config, "MACHINE_ROLE", "node")
    monkeypatch.setattr(dashboard_config, "HUB_URL", "http://hub.invalid")
    monkeypatch.setattr(health.dashboard_config, "MACHINE_ROLE", "node")
    monkeypatch.setattr(health.dashboard_config, "HUB_URL", "http://hub.invalid")
    monkeypatch.setattr(proxy_utils, "hub_in_cooldown", lambda: False)
    return direct, upstream


def request(query: bytes = b"") -> Request:
    return Request({"type": "http", "method": "GET", "path": "/api/health", "headers": [], "query_string": query})


@pytest.mark.asyncio
async def test_spoke_health_uses_hub_github_summary_and_local_identity(spoke: tuple[AsyncMock, AsyncMock]) -> None:
    direct, upstream = spoke
    incoming = request()
    body = await health.health_check(incoming)

    direct.assert_not_awaited()
    upstream.assert_awaited_once_with(incoming, require_success=True)
    assert body["status"] == "healthy"
    assert body["github_api"] == "connected"
    assert body["runners_registered"] == 23
    assert body["github_auth_source"] == "github_app"
    assert body["hostname"] == "spoke-machine"
    assert body["deployment"] == {"git_sha": "local-deployment"}
    assert body["dashboard_uptime_seconds"] >= 60
    assert "oauth" in body and "staff_availability" in body


@pytest.mark.asyncio
async def test_spoke_propagates_degraded_hub(spoke: tuple[AsyncMock, AsyncMock]) -> None:
    direct, upstream = spoke
    upstream.return_value.update(
        status="degraded", github_api="unreachable", github_error_type="GhAuthError", runners_registered=0
    )
    body = await health.health_check(request())
    direct.assert_not_awaited()
    assert body["status"] == "degraded"
    assert body["github_api"] == "unreachable"
    assert body["github_error_type"] == "GhAuthError"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        None,
        {},
        {"status": "healthy", "github_api": "connected", "runners_registered": -1},
        {"status": "healthy", "github_api": "connected", "runners_registered": True},
        {"status": "healthy", "github_api": "connected", "runners_registered": "23"},
        {"status": "degraded", "github_api": "connected", "runners_registered": 23},
        {"status": "healthy", "github_api": "connected", "runners_registered": 23, "github_auth_source": {}},
        {"status": "healthy", "github_api": "connected", "runners_registered": 23, "github_error_type": []},
    ],
)
async def test_spoke_rejects_malformed_summary(spoke: tuple[AsyncMock, AsyncMock], invalid: object) -> None:
    direct, upstream = spoke
    upstream.return_value = invalid
    body = await health.health_check(request())
    direct.assert_not_awaited()
    assert body["status"] == "degraded"
    assert body["github_api"] == "unreachable"
    assert body["runners_registered"] == 0


@pytest.mark.asyncio
async def test_spoke_probe_latency_includes_hub_request(
    spoke: tuple[AsyncMock, AsyncMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    ticks = iter([100.0, 101.25])
    monkeypatch.setattr(health.time, "perf_counter", lambda: next(ticks))
    body = await health.health_check(request())
    assert body["github_check_seconds"] == 1.25


@pytest.mark.asyncio
async def test_spoke_hub_failure_does_not_retry_local_org_access(spoke: tuple[AsyncMock, AsyncMock]) -> None:
    direct, upstream = spoke
    upstream.side_effect = HTTPException(status_code=503, detail="Hub connection error")
    body = await health.health_check(request())
    direct.assert_not_awaited()
    assert body["status"] == "degraded"
    assert body["github_error_type"] == "HTTPException"


@pytest.mark.asyncio
async def test_spoke_failure_logs_safe_probe_context(
    spoke: tuple[AsyncMock, AsyncMock], caplog: pytest.LogCaptureFixture
) -> None:
    direct, upstream = spoke
    secret = secrets.token_urlsafe(32)
    upstream.side_effect = HTTPException(status_code=503, detail=secret)
    body = await health.health_check(request())
    assert body["github_api"] == "unreachable"
    record = next(record for record in caplog.records if record.message.startswith("github_health_probe_failed"))
    assert record.getMessage() == "github_health_probe_failed stage=hub_proxy error_type=HTTPException"
    assert secret not in record.getMessage()
    direct.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_hub_summary_logs_safe_validation_context(
    spoke: tuple[AsyncMock, AsyncMock], caplog: pytest.LogCaptureFixture
) -> None:
    _direct, upstream = spoke
    upstream.return_value = {"status": "healthy", "github_api": "connected", "runners_registered": "secret"}
    await health.health_check(request())
    assert any(
        record.getMessage() == "github_health_probe_failed stage=hub_summary error_type=ValueError"
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_successful_hub_probe_emits_no_failure_warning(
    spoke: tuple[AsyncMock, AsyncMock], caplog: pytest.LogCaptureFixture
) -> None:
    await health.health_check(request())
    assert not any(record.message.startswith("github_health_probe_failed") for record in caplog.records)


@pytest.mark.asyncio
async def test_local_probe_failure_logs_safe_context(
    spoke: tuple[AsyncMock, AsyncMock], caplog: pytest.LogCaptureFixture
) -> None:
    direct, upstream = spoke
    direct.side_effect = RuntimeError("token=do-not-log")
    await health.health_check(request(b"local=1"))
    assert any(
        record.getMessage() == "github_health_probe_failed stage=local_probe error_type=RuntimeError"
        for record in caplog.records
    )
    assert all("token=do-not-log" not in record.getMessage() for record in caplog.records)
    upstream.assert_not_awaited()


@pytest.mark.asyncio
async def test_spoke_cancellation_propagates(spoke: tuple[AsyncMock, AsyncMock]) -> None:
    direct, upstream = spoke
    upstream.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await health.health_check(request())
    direct.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("local", [False, True])
async def test_forwarded_spoke_health_stops_at_one_hop(spoke: tuple[AsyncMock, AsyncMock], local: bool) -> None:
    direct, upstream = spoke
    incoming = request(b"local=1" if local else b"")
    incoming.scope["headers"] = [(b"x-dashboard-health-hop", b"1")]
    body = await health.health_check(incoming)
    direct.assert_not_awaited()
    upstream.assert_not_awaited()
    assert body["status"] == "degraded"
    assert body["github_api"] == "unreachable"


@pytest.mark.asyncio
async def test_forwarded_hub_health_keeps_direct_probe(
    spoke: tuple[AsyncMock, AsyncMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    direct, upstream = spoke
    monkeypatch.setattr(dashboard_config, "MACHINE_ROLE", "hub")
    monkeypatch.setattr(health.dashboard_config, "MACHINE_ROLE", "hub")
    incoming = request()
    incoming.scope["headers"] = [(b"x-dashboard-health-hop", b"1")]
    await health.health_check(incoming)
    direct.assert_awaited_once()
    upstream.assert_not_awaited()


@pytest.mark.asyncio
async def test_spoke_open_circuit_does_not_probe_hub_or_local_org(
    spoke: tuple[AsyncMock, AsyncMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    direct, upstream = spoke
    monkeypatch.setattr(proxy_utils, "hub_in_cooldown", lambda: True)
    monkeypatch.setattr(health, "hub_in_cooldown", lambda: True)
    body = await health.health_check(request())
    direct.assert_not_awaited()
    upstream.assert_not_awaited()
    assert body["status"] == "degraded"
    assert body["hub_circuit_open"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["hub", "standalone", "local"])
async def test_direct_health_modes_keep_existing_probe(
    spoke: tuple[AsyncMock, AsyncMock], monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    direct, upstream = spoke
    if mode == "hub":
        monkeypatch.setattr(dashboard_config, "MACHINE_ROLE", "hub")
        monkeypatch.setattr(health.dashboard_config, "MACHINE_ROLE", "hub")
    elif mode == "standalone":
        monkeypatch.setattr(dashboard_config, "HUB_URL", None)
        monkeypatch.setattr(health.dashboard_config, "HUB_URL", None)
    body = await health.health_check(request(b"local=1" if mode == "local" else b""))
    upstream.assert_not_awaited()
    direct.assert_awaited_once()
    assert body["status"] == "degraded"


@pytest.mark.asyncio
async def test_hub_health_still_reports_direct_success(
    spoke: tuple[AsyncMock, AsyncMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    direct, upstream = spoke
    monkeypatch.setattr(dashboard_config, "MACHINE_ROLE", "hub")
    monkeypatch.setattr(health.dashboard_config, "MACHINE_ROLE", "hub")
    direct.side_effect = None
    direct.return_value = {"runners": [{"id": 1}], "total_count": 1}
    body = await health.health_check(request())
    upstream.assert_not_awaited()
    assert body["status"] == "healthy"
    assert body["runners_registered"] == 1
