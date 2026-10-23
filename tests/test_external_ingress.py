from __future__ import annotations

import logging

from external_ingress import _is_external_ingress, deny_external_ingress_bearer
from fastapi import Request


def _request(headers: list[tuple[bytes, bytes]], *, path: str = "/api/v1/staff/threads") -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)


def test_cloudflare_marked_bearer_is_denied_without_logging_secret(caplog) -> None:
    request = _request([(b"authorization", b"Bearer must-not-log"), (b"cf-ray", b"ray-1")])

    with caplog.at_level(logging.WARNING):
        response = deny_external_ingress_bearer(request, logging.getLogger("external_ingress"))

    assert response is not None and response.status_code == 403
    assert response.body == b'{"error":"Request not permitted"}'
    assert response.headers["X-Request-ID"] == request.state.request_id
    assert "must-not-log" not in caplog.text


def test_local_origin_bearer_is_not_external_ingress() -> None:
    request = _request([(b"authorization", b"Bearer local"), (b"origin", b"http://localhost:8321")])

    assert not _is_external_ingress(request)
    assert deny_external_ingress_bearer(request, logging.getLogger("external_ingress")) is None


def test_malformed_origin_fails_closed_for_bearer() -> None:
    request = _request([(b"authorization", b"Bearer token"), (b"origin", b"http://[invalid")])

    assert _is_external_ingress(request)
    assert deny_external_ingress_bearer(request, logging.getLogger("external_ingress")) is not None
