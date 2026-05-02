"""Tests for backend/request_context.py — issue #386."""

from __future__ import annotations

import logging

import request_context as rc

# ---------------------------------------------------------------------------
# Context variables defaults
# ---------------------------------------------------------------------------


def test_request_id_default_is_dash() -> None:
    assert rc.request_id_ctx.get() == "-"


def test_principal_id_default_is_dash() -> None:
    assert rc.principal_id_ctx.get() == "-"


# ---------------------------------------------------------------------------
# _new_request_id
# ---------------------------------------------------------------------------


def test_new_request_id_length() -> None:
    rid = rc._new_request_id()
    assert len(rid) == 12


def test_new_request_id_is_hex() -> None:
    rid = rc._new_request_id()
    int(rid, 16)  # raises ValueError if not hex


def test_new_request_id_unique() -> None:
    ids = {rc._new_request_id() for _ in range(20)}
    assert len(ids) == 20  # all unique


# ---------------------------------------------------------------------------
# current_request_id — outside request context
# ---------------------------------------------------------------------------


def test_current_request_id_outside_context() -> None:
    assert rc.current_request_id() == "-"


def test_current_request_id_within_context() -> None:
    token = rc.request_id_ctx.set("test-abc123")
    try:
        assert rc.current_request_id() == "test-abc123"
    finally:
        rc.request_id_ctx.reset(token)


# ---------------------------------------------------------------------------
# RequestIdLogFilter
# ---------------------------------------------------------------------------


def test_request_id_log_filter_injects_fields() -> None:
    f = rc.RequestIdLogFilter()
    record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
    f.filter(record)
    assert hasattr(record, "request_id")
    assert hasattr(record, "principal_id")
    assert record.request_id == "-"


def test_request_id_log_filter_returns_true() -> None:
    f = rc.RequestIdLogFilter()
    record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
    assert f.filter(record) is True


# ---------------------------------------------------------------------------
# RequestIdMiddleware — tested via FastAPI TestClient
# ---------------------------------------------------------------------------


def test_request_id_middleware_adds_header() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.add_middleware(rc.RequestIdMiddleware)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/ping")
    assert "X-Request-ID" in resp.headers
    assert len(resp.headers["X-Request-ID"]) > 0


def test_request_id_middleware_propagates_inbound_id() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.add_middleware(rc.RequestIdMiddleware)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/ping", headers={"X-Request-ID": "my-custom-id"})
    assert resp.headers.get("X-Request-ID") == "my-custom-id"
