"""Tests for unified ErrorResponse handler (issue #717)."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from error_models import ErrorResponse, from_http_exception, not_ready, internal_error, upstream_error


class FakeHTTPException:
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail


def test_from_http_exception_404():
    exc = FakeHTTPException(404, "resource not found")
    result = from_http_exception(exc)
    assert result.error == "not_found"
    assert result.detail == "resource not found"


def test_from_http_exception_403():
    exc = FakeHTTPException(403, "forbidden")
    result = from_http_exception(exc)
    assert result.error == "forbidden"


def test_from_http_exception_500():
    exc = FakeHTTPException(500, "server error")
    result = from_http_exception(exc)
    assert result.error == "internal_error"


def test_from_http_exception_unknown_status():
    exc = FakeHTTPException(418, "I'm a teapot")
    result = from_http_exception(exc)
    assert result.error == "server_error"
    assert "418" in result.detail or "teapot" in result.detail.lower()


def test_from_http_exception_requires_status_code():
    """Pre-condition: exc must have status_code."""
    class BadExc:
        detail = "nope"
    with pytest.raises(AssertionError):
        from_http_exception(BadExc())


def test_from_http_exception_requires_detail():
    """Pre-condition: exc must have detail attribute."""
    class BadExc:
        status_code = 404
    with pytest.raises(AssertionError):
        from_http_exception(BadExc())


def test_not_ready_factory():
    result = not_ready("no online runners")
    assert result.error == "not_ready"
    assert "runners" in result.detail


def test_internal_error_factory():
    result = internal_error("unexpected failure")
    assert result.error == "internal_error"


def test_upstream_error_factory():
    result = upstream_error("remote service failed")
    assert result.error == "upstream_error"


def test_error_response_model_validates():
    r = ErrorResponse(error="not_found", detail="thing not found")
    assert r.error == "not_found"
    assert r.request_id is None


def test_all_error_kinds_are_strings():
    from error_models import (not_found, validation_error, server_error,
                               bad_gateway, rate_limited, forbidden, conflict)
    for factory in [not_found, validation_error, server_error, bad_gateway,
                    rate_limited, forbidden, conflict]:
        result = factory("test detail")
        assert isinstance(result.error, str)
        assert len(result.error) > 0


def test_from_http_exception_400():
    exc = FakeHTTPException(400, "bad request")
    result = from_http_exception(exc)
    assert result.error == "validation_error"


def test_from_http_exception_429():
    exc = FakeHTTPException(429, "too many requests")
    result = from_http_exception(exc)
    assert result.error == "rate_limited"


def test_from_http_exception_502():
    exc = FakeHTTPException(502, "bad gateway")
    result = from_http_exception(exc)
    assert result.error == "bad_gateway"


def test_from_http_exception_503():
    exc = FakeHTTPException(503, "service unavailable")
    result = from_http_exception(exc)
    assert result.error == "not_ready"


def test_from_http_exception_with_request_id():
    exc = FakeHTTPException(404, "not found")
    result = from_http_exception(exc, request_id="req-123")
    assert result.request_id == "req-123"
