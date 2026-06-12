"""Tests for the extracted metrics module (issue #159).

Structural and integration tests for the backend/metrics.py router.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))


def test_metrics_module_importable() -> None:
    import metrics  # noqa: PLC0415

    assert hasattr(metrics, "router"), "metrics.py must export 'router'"


def test_metrics_router_has_expected_routes() -> None:
    """#940: metrics.py owns only the unique /api/disk/pool-pressure route. The
    previously-duplicated /api/system and /api/fleet/status handlers were removed
    because they shadowed the maintained routers.system / routers.fleet copies."""
    import metrics  # noqa: PLC0415

    paths = {r.path for r in metrics.router.routes}  # type: ignore[attr-defined]
    assert "/api/disk/pool-pressure" in paths, "metrics router must expose /api/disk/pool-pressure"
    assert "/api/system" not in paths, "/api/system is owned by routers.system, not metrics (#940)"
    assert "/api/fleet/status" not in paths, "/api/fleet/status is owned by routers.fleet, not metrics (#940)"


def test_metrics_router_not_inline_in_server() -> None:
    """The metrics endpoints must not be defined inline in server.py."""
    server_src = (_BACKEND_DIR / "server.py").read_text(encoding="utf-8")
    assert '@app.get("/api/system")' not in server_src, "/api/system must be in router, not inline"
    assert '@app.get("/api/fleet/status")' not in server_src, "/api/fleet/status must be in router, not inline"


def test_server_registers_metrics_router() -> None:
    """server.py must include the metrics router."""
    server_src = (_BACKEND_DIR / "server.py").read_text(encoding="utf-8")
    assert "import metrics as _metrics_router" in server_src
    assert "include_router(_metrics_router.router)" in server_src


def test_metrics_does_not_import_from_server() -> None:
    """#940: metrics.py must not import from backend.server. The only reason it
    ever did was the duplicate /api/fleet/status handler (now removed), which
    forced a lazy circular import."""
    metrics_src = (_BACKEND_DIR / "metrics.py").read_text(encoding="utf-8")
    assert "from server import" not in metrics_src, "metrics.py must not import from server (#940)"
    assert "import server" not in metrics_src, "metrics.py must not import server (#940)"
    assert "psutil," not in metrics_src
