"""Tests for MaxBodySizeMiddleware (issue #350; hardened for #4274 WP19 F-07).

F-07: the request body limit was bypassable three ways:
  1. no (or an understated) Content-Length: only the header was checked;
  2. a client-supplied ``X-Max-Body-Size`` request header raised the cap;
  3. ``@limit_body_size`` route caps were never resolved before routing.
The middleware is driven at ASGI level so chunked bodies can be streamed.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI, Request

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import middleware as mw  # noqa: E402
from middleware import (  # noqa: E402
    DEFAULT_MAX_BODY_SIZE,
    WEBHOOK_MAX_BODY_SIZE,
    MaxBodySizeMiddleware,
    limit_body_size,
)

_DEFAULT = 100  # test default cap (bytes)
_TIGHT = 40
_LARGE = 400


def _make_app(seen: list[int]) -> FastAPI:
    """App with a default-capped, a tighter and an explicitly larger route."""
    app = FastAPI()
    router = APIRouter(prefix="/api")

    async def _read(request: Request) -> dict[str, int]:
        body = await request.body()
        seen.append(len(body))
        return {"n": len(body)}

    @router.api_route("/echo", methods=["GET", "POST"])
    async def echo(request: Request) -> dict[str, int]:
        return await _read(request)

    @router.post("/tight")
    @limit_body_size(_TIGHT)
    async def tight(request: Request) -> dict[str, int]:
        return await _read(request)

    @router.post("/large")
    @limit_body_size(_LARGE)
    async def large(request: Request) -> dict[str, int]:
        return await _read(request)

    @router.post("/json")
    async def json_route(payload: dict[str, Any]) -> dict[str, int]:
        seen.append(len(payload))
        return {"n": len(payload)}

    @router.post("/swallow")
    async def swallow(request: Request) -> dict[str, bool]:
        try:
            await request.body()
        except Exception:  # noqa: BLE001 - a handler must not be able to mask the 413
            pass
        return {"swallowed": True}

    app.include_router(router)

    @app.post("/api/nobody")  # never reads its body
    async def nobody() -> dict[str, bool]:
        seen.append(-1)
        return {"ok": True}

    @app.middleware("http")  # like server.py: BaseHTTPMiddleware layers sit inside the limit
    async def inner_layer(request: Request, call_next: Any) -> Any:
        return await call_next(request)

    app.add_middleware(MaxBodySizeMiddleware, default_limit=_DEFAULT)
    return app


def _call(
    app: Any,
    path: str,
    chunks: list[bytes],
    *,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    declare_length: bool = False,
) -> tuple[int, int]:
    """Drive *app* over raw ASGI; return (status, number of body chunks pulled)."""
    hdrs = dict(headers or {})
    if declare_length:
        hdrs["content-length"] = str(sum(len(c) for c in chunks))
    queue = list(chunks) or [b""]
    pulled = 0
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal pulled
        if not queue:
            return {"type": "http.disconnect"}
        pulled += 1
        return {"type": "http.request", "body": queue.pop(0), "more_body": bool(queue)}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in hdrs.items()],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert len(starts) == 1, sent
    return starts[0]["status"], pulled


_TEN_CHUNKS = [b"x" * 50] * 10  # 500 bytes, 5x the default cap


# --- F-07 bypass 1: only the Content-Length header was checked ------------- #


@pytest.mark.parametrize("method", ["POST", "GET"])
def test_f07_chunked_body_without_content_length_is_capped(method: str) -> None:
    seen: list[int] = []
    status, pulled = _call(_make_app(seen), "/api/echo", list(_TEN_CHUNKS), method=method)
    assert status == 413
    assert seen == []  # the handler never got the oversized body
    assert pulled <= 3  # rejected as soon as the cap was crossed, not after buffering


def test_f07_understated_content_length_is_capped() -> None:
    seen: list[int] = []
    status, pulled = _call(_make_app(seen), "/api/echo", list(_TEN_CHUNKS), headers={"content-length": "10"})
    assert (status, seen) == (413, [])
    assert pulled <= 3


def test_f07_chunked_json_body_route_returns_413_not_400() -> None:
    seen: list[int] = []
    chunks = [b'{"k": "'] + [b"x" * 50] * 10 + [b'"}']
    status, _ = _call(_make_app(seen), "/api/json", chunks, headers={"content-type": "application/json"})
    assert (status, seen) == (413, [])


def test_f07_handler_cannot_swallow_the_rejection() -> None:
    status, _ = _call(_make_app([]), "/api/swallow", list(_TEN_CHUNKS))
    assert status == 413


# --- F-07 bypass 2: client-controlled X-Max-Body-Size header --------------- #


@pytest.mark.parametrize("declare_length", [True, False])
def test_f07_client_header_cannot_raise_the_limit(declare_length: bool) -> None:
    seen: list[int] = []
    status, _ = _call(
        _make_app(seen),
        "/api/echo",
        list(_TEN_CHUNKS),
        headers={"X-Max-Body-Size": str(10**9)},
        declare_length=declare_length,
    )
    assert (status, seen) == (413, [])


# --- F-07 bypass 3: @limit_body_size caps were never applied --------------- #


@pytest.mark.parametrize("declare_length", [True, False])
def test_f07_route_decorator_tighter_cap_is_enforced(declare_length: bool) -> None:
    seen: list[int] = []
    app = _make_app(seen)
    over = [b"x" * 30, b"x" * 30]  # 60 bytes: under the default, over the route cap
    assert _call(app, "/api/tight", over, declare_length=declare_length)[0] == 413
    assert seen == []
    assert _call(app, "/api/tight", [b"x" * _TIGHT], declare_length=declare_length)[0] == 200
    assert seen == [_TIGHT]


@pytest.mark.parametrize("declare_length", [True, False])
def test_explicit_larger_route_limit_is_honoured_and_bounded(declare_length: bool) -> None:
    seen: list[int] = []
    app = _make_app(seen)
    body = [b"x" * 100] * 4  # 400 bytes: 4x the default, exactly the route cap
    assert _call(app, "/api/large", list(body), declare_length=declare_length)[0] == 200
    assert seen == [_LARGE]
    assert _call(app, "/api/large", [*body, b"x"], declare_length=declare_length)[0] == 413
    # The larger cap is scoped to its own route.
    assert _call(app, "/api/echo", list(body), declare_length=declare_length)[0] == 413
    assert seen == [_LARGE]


def test_real_routes_declare_their_caps() -> None:
    from routers import voice  # noqa: PLC0415
    from routers.linear_webhook import linear_webhook  # noqa: PLC0415
    from routers.staff_usage import ingest_usage_snapshot  # noqa: PLC0415

    assert mw.endpoint_body_limit(linear_webhook) == WEBHOOK_MAX_BODY_SIZE
    assert mw.endpoint_body_limit(ingest_usage_snapshot) == WEBHOOK_MAX_BODY_SIZE
    assert mw.endpoint_body_limit(voice.transcribe_voice) == voice.MAX_AUDIO_BYTES > DEFAULT_MAX_BODY_SIZE


# --- Unchanged behaviour for legitimate requests --------------------------- #


@pytest.mark.parametrize("declare_length", [True, False])
def test_body_at_the_limit_is_delivered_intact(declare_length: bool) -> None:
    seen: list[int] = []
    status, pulled = _call(_make_app(seen), "/api/echo", [b"x" * 50, b"y" * 50], declare_length=declare_length)
    assert (status, seen, pulled) == (200, [_DEFAULT], 2)


def test_declared_oversize_is_rejected_before_the_body_is_read() -> None:
    seen: list[int] = []
    status, pulled = _call(_make_app(seen), "/api/nobody", list(_TEN_CHUNKS), declare_length=True)
    assert (status, pulled, seen) == (413, 0, [])  # the handler did not run


def test_bodyless_get_passes() -> None:
    seen: list[int] = []
    assert _call(_make_app(seen), "/api/echo", [], method="GET")[0] == 200
    assert seen == [0]


@pytest.mark.parametrize("value", ["not-a-number", "-1", "1, 2"])
def test_invalid_content_length_returns_400(value: str) -> None:
    status, pulled = _call(_make_app([]), "/api/echo", [b"x"], headers={"content-length": value})
    assert (status, pulled) == (400, 0)


def test_non_http_scopes_pass_through_untouched() -> None:
    calls: list[tuple[Any, Any]] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        calls.append((receive, send))

    asyncio.run(MaxBodySizeMiddleware(inner)({"type": "websocket"}, "recv", "send"))
    assert calls == [("recv", "send")]


def test_limit_marker_resolves_through_wrapped_chain() -> None:
    @limit_body_size(1000)
    async def real_handler() -> None: ...

    async def wrapper() -> None: ...

    wrapper.__wrapped__ = real_handler  # type: ignore[attr-defined]
    assert mw.endpoint_body_limit(wrapper) == 1000
    assert mw.endpoint_body_limit(lambda: None) is None


# --- Contracts and wiring -------------------------------------------------- #


@pytest.mark.parametrize("bad", [0, -1, True, "64"])
def test_limits_must_be_positive_integers(bad: Any) -> None:
    with pytest.raises(ValueError):
        limit_body_size(bad)
    with pytest.raises(ValueError):
        MaxBodySizeMiddleware(lambda *a: None, default_limit=bad)


def test_default_limit_is_one_megabyte() -> None:
    assert MaxBodySizeMiddleware(lambda *a: None).default_limit == DEFAULT_MAX_BODY_SIZE == 1024 * 1024


def test_server_wires_a_single_enforcement_point_before_csrf() -> None:
    """server.py registers the middleware once, outside auth/CORS, inside CSRF."""
    src = (_BACKEND_DIR / "server.py").read_text(encoding="utf-8")
    assert src.count("app.add_middleware(MaxBodySizeMiddleware") == 1
    assert "max_body_size_check" not in src
    body_idx = src.index("app.add_middleware(MaxBodySizeMiddleware")
    assert src.index("CORSMiddleware,") < body_idx < src.index("async def _csrf_check")
    assert not hasattr(mw, "max_body_size_check")
