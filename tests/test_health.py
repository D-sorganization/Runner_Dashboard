"""Tests for backend/health.py — issue #386.

health.py is a thin FastAPI router that lazily imports server internals.
We can test the router object without spawning the full server.
"""

from __future__ import annotations

import sys
import time
import types
from pathlib import Path

import pytest
from fastapi import APIRouter

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import health as h  # noqa: E402


def test_router_is_apiRouter() -> None:
    assert isinstance(h.router, APIRouter)


def test_router_has_health_routes() -> None:
    paths = [r.path for r in h.router.routes]  # type: ignore[attr-defined]
    # At minimum a /health or /api/health route must exist
    assert any("health" in p for p in paths), f"No health route found in {paths}"


@pytest.mark.asyncio
async def test_api_health_uses_supported_github_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    """Health polling must call the shared GitHub helper with its supported signature."""
    seen: dict[str, str] = {}
    metrics: list[tuple[str, str, float]] = []

    async def fake_gh_api_admin(endpoint: str) -> dict:
        seen["endpoint"] = endpoint
        raise RuntimeError("network unavailable")

    fake_server = types.SimpleNamespace(
        BOOT_TIME=time.time(),
        HOSTNAME="test-host",
        ORG="D-sorganization",
        _cache_get=lambda *_args, **_kwargs: None,
        _cache_set=lambda *_args, **_kwargs: None,
        _deployment_info=lambda: {"version": "test"},
        gh_api_admin=fake_gh_api_admin,
    )
    monkeypatch.setitem(sys.modules, "server", fake_server)
    monkeypatch.setattr(
        h,
        "record_dashboard_health",
        lambda status, github_api, duration_s: metrics.append((status, github_api, duration_s)),
    )

    body = await h._health_impl()  # noqa: SLF001

    assert body["status"] == "degraded"
    assert body["github_api"] == "unreachable"
    assert body["github_error_type"] == "RuntimeError"
    assert body["github_check_seconds"] >= 0
    assert seen["endpoint"] == "/orgs/D-sorganization/actions/runners?per_page=100&page=1"
    assert metrics
    assert metrics[0][0:2] == ("degraded", "unreachable")
    assert metrics[0][2] >= 0
