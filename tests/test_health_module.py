"""Tests for the extracted health module (issue #159).

Structural and integration tests for the backend/health.py router.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))


def test_health_module_importable() -> None:
    import health  # noqa: PLC0415

    assert hasattr(health, "router"), "health.py must export 'router'"


def test_health_router_has_expected_routes() -> None:
    import health  # noqa: PLC0415

    paths = {r.path for r in health.router.routes}  # type: ignore[attr-defined]
    assert "/api/health" in paths, "health router must expose /api/health"
    assert "/health" in paths, "health router must expose /health"


def test_health_router_not_inline_in_server() -> None:
    """The health endpoints must not be defined inline in server.py."""
    server_src = (_BACKEND_DIR / "server.py").read_text(encoding="utf-8")
    assert '@app.get("/api/health")' not in server_src, "/api/health must be in router, not inline"
    assert '@app.get("/health"' not in server_src, "/health must be in router, not inline"


def test_server_registers_health_router() -> None:
    """server.py must include the health router."""
    server_src = (_BACKEND_DIR / "server.py").read_text(encoding="utf-8")
    assert "import health as _health_router" in server_src
    assert "include_router(_health_router.router)" in server_src


def test_health_impl_exported() -> None:
    """The _health_impl function must be callable from the module."""
    import health  # noqa: PLC0415

    assert callable(health._health_impl), "_health_impl must be callable"


def test_health_router_has_readyz_route() -> None:
    """The health router must expose /readyz."""
    import health  # noqa: PLC0415

    paths = {r.path for r in health.router.routes}  # type: ignore[attr-defined]
    assert "/readyz" in paths, "health router must expose /readyz"


def test_readyz_reports_session_secret_source(monkeypatch) -> None:
    """GET /readyz must return a valid session_secret_source field."""
    import dashboard_config  # noqa: PLC0415

    for source in ("env", "persisted", "generated"):
        monkeypatch.setattr(dashboard_config, "SESSION_SECRET_SOURCE", source)

        import health  # noqa: PLC0415

        paths = {r.path: r for r in health.router.routes}  # type: ignore[attr-defined]
        assert "/readyz" in paths, "/readyz route must exist"
