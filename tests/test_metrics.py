"""Tests for backend/metrics.py — issue #386.

metrics.py is a thin FastAPI router that delegates to server.py internals via
lazy imports (to avoid circular dependencies). We test only what can be
exercised without spinning up a full server: the router object itself and any
module-level constants / helpers.
"""

from __future__ import annotations

import metrics as m
from fastapi import APIRouter


def test_router_is_apiRouter() -> None:
    assert isinstance(m.router, APIRouter)


def test_router_has_system_route() -> None:
    paths = [r.path for r in m.router.routes]  # type: ignore[attr-defined]
    assert "/api/system" in paths
