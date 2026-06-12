"""Tests for backend/metrics.py — issue #386.

metrics.py is a thin FastAPI router. As of issue #940 it owns only the unique
``/api/disk/pool-pressure`` route; the previously-duplicated ``/api/system`` and
``/api/fleet/status`` handlers were removed because they shadowed the maintained
implementations in routers.system / routers.fleet (first-match-wins routing).
We test only what can be exercised without spinning up a full server: the router
object itself and any module-level helpers.
"""

from __future__ import annotations

import metrics as m
from fastapi import APIRouter


def test_router_is_apiRouter() -> None:
    assert isinstance(m.router, APIRouter)


def test_router_owns_pool_pressure_route() -> None:
    paths = [r.path for r in m.router.routes]  # type: ignore[attr-defined]
    assert "/api/disk/pool-pressure" in paths


def test_router_no_longer_registers_shadowing_routes() -> None:
    """#940: metrics.py must not re-register /api/system or /api/fleet/status,
    which are owned by routers.system and routers.fleet respectively."""
    paths = [r.path for r in m.router.routes]  # type: ignore[attr-defined]
    assert "/api/system" not in paths
    assert "/api/fleet/status" not in paths


def test_resolve_windows_host_disk_path_prefers_configured_existing_path(tmp_path) -> None:
    assert m._resolve_windows_host_disk_path(tmp_path) == tmp_path


def test_resolve_windows_host_disk_path_returns_none_when_no_candidate_exists(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(m.Path, "exists", lambda self: False)

    assert m._resolve_windows_host_disk_path(tmp_path / "missing") is None
