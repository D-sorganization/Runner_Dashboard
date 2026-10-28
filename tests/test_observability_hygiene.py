"""Tests for Private Mode PM-3 observability hygiene (#3253)."""

from __future__ import annotations

import logging
import sys
from io import StringIO
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from observability_hygiene import (  # noqa: E402
    ClassificationLevel,
    classification_level_ctx,
    format_exception_for_log,
    metrics_route_templates_enabled,
    sanitize_metric_labels,
    template_github_api_endpoint,
    template_http_metric_endpoint,
)


def test_metrics_route_templates_flag() -> None:
    assert metrics_route_templates_enabled({}) is False
    assert metrics_route_templates_enabled({"RD_METRICS_ROUTE_TEMPLATES": "0"}) is False
    assert metrics_route_templates_enabled({"RD_METRICS_ROUTE_TEMPLATES": "on"}) is True


def test_flag_off_templates_unchanged() -> None:
    raw = "/api/runs/deadbeefcafe"
    assert template_http_metric_endpoint(None, raw, enabled=False) == raw
    endpoint = "/repos/Example-Corp/secret-widget/issues/42"
    assert template_github_api_endpoint(endpoint, enabled=False) == endpoint


def test_http_endpoint_templates_numeric_segments_when_flag_on() -> None:
    raw = "/api/runs/deadbeefcafe/details"
    assert template_http_metric_endpoint(None, raw, enabled=True) == "/api/runs/{id}/details"


def test_http_endpoint_prefers_route_template_when_flag_on() -> None:
    raw = "/api/issues/3253"
    assert (
        template_http_metric_endpoint("/api/issues/{issue_id}", raw, enabled=True)
        == "/api/issues/{issue_id}"
    )


def test_github_endpoint_strips_repo_and_issue_numbers() -> None:
    endpoint = "/repos/Example-Corp/widget/issues/3253/comments"
    templated = template_github_api_endpoint(endpoint, enabled=True)
    assert "Example-Corp" not in templated
    assert "widget" not in templated
    assert "3253" not in templated
    assert templated == "/repos/{owner}/{repo}/issues/{issue_number}/comments"
    pulls = template_github_api_endpoint("/repos/Example-Corp/widget/pulls/99", enabled=True)
    assert pulls == "/repos/{owner}/{repo}/pulls/{pull_number}"


def test_no_raw_path_repo_or_issue_in_sanitized_labels() -> None:
    labels = {
        "method": "GET",
        "endpoint": "/repos/Example-Corp/widget/issues/7",
        "status_code": "200",
        "repo": "Example-Corp/widget",
        "issue": "7",
    }
    clean = sanitize_metric_labels(
        "dashboard_http_requests_total",
        labels,
        templates_enabled=True,
    )
    joined = " ".join(f"{k}={v}" for k, v in clean.items())
    assert "Example-Corp" not in joined
    assert "widget" not in joined
    assert "/issues/7" not in joined
    assert "issue" not in clean
    assert "repo" not in clean
    assert clean["endpoint"] == "/repos/{owner}/{repo}/issues/{issue_number}"


def test_label_allowlist_drops_unknown_labels() -> None:
    labels = {
        "method": "GET",
        "endpoint": "/metrics",
        "status_code": "200",
        "thread_id": "secret-thread",
    }
    clean = sanitize_metric_labels(
        "dashboard_http_requests_total",
        labels,
        templates_enabled=True,
    )
    assert "thread_id" not in clean
    assert set(clean) == {"method", "endpoint", "status_code"}


def test_cardinality_bound_truncates_overlong_label_values() -> None:
    long_path = "/api/" + ("x" * 300)
    labels = {"method": "GET", "endpoint": long_path, "status_code": "200"}
    clean = sanitize_metric_labels(
        "dashboard_http_requests_total",
        labels,
        templates_enabled=True,
    )
    assert len(clean["endpoint"]) <= 120


def test_sensitive_level_filters_sub_warning_log_records() -> None:
    from observability_hygiene import ClassificationLevelLogFilter  # noqa: PLC0415

    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))
    handler.addFilter(ClassificationLevelLogFilter())
    logger = logging.getLogger("test_observability_hygiene.filter")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    token = classification_level_ctx.set(ClassificationLevel.SENSITIVE)
    try:
        logger.info("should not appear")
        logger.warning("should appear")
    finally:
        classification_level_ctx.reset(token)

    output = stream.getvalue()
    assert "should not appear" not in output
    assert "should appear" in output


def test_exception_with_sensitive_args_logs_no_values(caplog: pytest.LogCaptureFixture) -> None:
    from observability_hygiene import log_exception_scrubbed  # noqa: PLC0415

    caplog.set_level(logging.ERROR)
    token = classification_level_ctx.set(ClassificationLevel.SENSITIVE)
    secret = "Example-Corp-ultra-secret-token"  # pragma: allowlist secret
    try:
        try:
            raise ValueError(secret, "metadata-only")
        except ValueError:
            log_exception_scrubbed(logging.getLogger("test_observability_hygiene.exc"), "failed")
    finally:
        classification_level_ctx.reset(token)

    text = caplog.text
    assert secret not in text
    assert "metadata-only" not in text


def test_format_exception_for_log_scrubs_sensitive_args_at_sensitive_level() -> None:
    token = classification_level_ctx.set(ClassificationLevel.SENSITIVE)
    try:
        try:
            raise RuntimeError("repo=Example-Corp/widget issue=99")
        except RuntimeError:
            import sys

            formatted = format_exception_for_log(*sys.exc_info())
    finally:
        classification_level_ctx.reset(token)

    assert "Example-Corp" not in formatted
    assert "widget" not in formatted
    assert "99" not in formatted or "{issue_number}" in formatted


def test_public_level_exception_format_unchanged() -> None:
    token = classification_level_ctx.set(ClassificationLevel.PUBLIC)
    try:
        try:
            raise RuntimeError("visible-detail")
        except RuntimeError:
            import sys

            formatted = format_exception_for_log(*sys.exc_info())
    finally:
        classification_level_ctx.reset(token)

    assert "visible-detail" in formatted


def test_prometheus_middleware_flag_off_uses_raw_path_without_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import prometheus_metrics as pm  # noqa: PLC0415

    if not pm._PROMETHEUS_AVAILABLE:
        pytest.skip("prometheus_client not installed")

    monkeypatch.setattr(pm, "metrics_route_templates_enabled", lambda _env=None: False)
    observed: list[str] = []
    original_labels = pm.HTTP_REQUESTS_TOTAL.labels

    def _capture_labels(**kwargs: str):  # noqa: ANN003
        observed.append(kwargs.get("endpoint", ""))
        return original_labels(**kwargs)

    monkeypatch.setattr(pm.HTTP_REQUESTS_TOTAL, "labels", _capture_labels)

    class _App:
        async def __call__(self, scope, receive, send):  # noqa: ANN001
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

    middleware = pm.PrometheusMiddleware(_App())

    async def _run() -> None:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/items/secret-id",
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_message):  # noqa: ANN001
            return None

        await middleware(scope, receive, send)

    import asyncio

    asyncio.run(_run())
    assert observed == ["/api/items/secret-id"]
