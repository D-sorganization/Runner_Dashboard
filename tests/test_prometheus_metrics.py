"""Tests for the Prometheus /metrics endpoint (issue #330)."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def test_prometheus_metrics_module_importable() -> None:
    import prometheus_metrics  # noqa: PLC0415

    assert hasattr(prometheus_metrics, "router"), "prometheus_metrics.py must export 'router'"


def test_prometheus_metrics_endpoint_registered() -> None:
    import prometheus_metrics  # noqa: PLC0415

    paths = {r.path for r in prometheus_metrics.router.routes}
    assert "/metrics" in paths, "prometheus_metrics router must expose /metrics"


def test_server_registers_prometheus_router() -> None:
    server_src = (_BACKEND / "server.py").read_text(encoding="utf-8")
    assert "import prometheus_metrics as _prometheus_metrics_router" in server_src
    assert "include_router(_prometheus_metrics_router.router)" in server_src


def test_server_wires_prometheus_middleware() -> None:
    server_src = (_BACKEND / "server.py").read_text(encoding="utf-8")
    assert "PrometheusMiddleware" in server_src


def test_prometheus_metrics_endpoint_returns_text(mock_auth) -> None:  # noqa: ARG001
    """The /metrics endpoint must return a 200 with text/plain or 503 when library absent."""
    import server  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    client = TestClient(server.app)
    response = client.get("/metrics")
    assert response.status_code in (200, 503)
    assert "text/plain" in response.headers.get("content-type", "")


def test_record_gh_api_call_does_not_raise() -> None:
    from prometheus_metrics import record_gh_api_call  # noqa: PLC0415

    # Should not raise even if prometheus_client is available or not
    record_gh_api_call("GET", "/repos/D-sorganization/runner-dashboard/runs", 0.123)


def test_record_cache_hit_miss_do_not_raise() -> None:
    from prometheus_metrics import record_cache_hit, record_cache_miss  # noqa: PLC0415

    record_cache_hit("main")
    record_cache_miss("main")


def test_helper_functions_exported() -> None:
    import prometheus_metrics  # noqa: PLC0415

    assert callable(prometheus_metrics.record_gh_api_call)
    assert callable(prometheus_metrics.record_cache_hit)
    assert callable(prometheus_metrics.record_cache_miss)
    assert callable(prometheus_metrics.update_lease_gauge)
    assert callable(prometheus_metrics.record_lease_expired)


def test_prometheus_middleware_class_exported() -> None:
    from prometheus_metrics import PrometheusMiddleware  # noqa: PLC0415

    assert callable(PrometheusMiddleware)
