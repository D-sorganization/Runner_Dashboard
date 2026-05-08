"""Tests for backend/health.py — issue #386.

health.py is a thin FastAPI router that lazily imports server internals.
We can test the router object without spawning the full server.
"""

from __future__ import annotations

import health as h
from fastapi import APIRouter


def test_router_is_apiRouter() -> None:
    assert isinstance(h.router, APIRouter)


def test_router_has_health_routes() -> None:
    paths = [r.path for r in h.router.routes]  # type: ignore[attr-defined]
    # At minimum a /health or /api/health route must exist
    assert any("health" in p for p in paths), f"No health route found in {paths}"
