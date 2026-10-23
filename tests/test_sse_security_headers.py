"""Exercise actual streamed responses through the shared response middleware."""

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient
from middleware import add_security_headers


@pytest.mark.parametrize("content_type", ["text/event-stream", "text/event-stream; charset=utf-8"])
def test_sse_disables_proxy_buffering_and_transformation(content_type: str) -> None:
    app = FastAPI()
    app.middleware("http")(add_security_headers)

    @app.get("/stream")
    def stream() -> StreamingResponse:
        return StreamingResponse(
            iter(["data: ready\n\n"]),
            headers={
                "Content-Type": content_type,
                "Cache-Control": "public, max-age=3600",
                "X-Accel-Buffering": "yes",
            },
        )

    with TestClient(app) as client:
        with client.stream("GET", "/stream") as response:
            assert response.headers["X-Accel-Buffering"] == "no"
            assert response.headers["Cache-Control"] == "no-cache, no-transform"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert "data: ready" in response.read().decode()


def test_non_streaming_response_retains_existing_cache_policy() -> None:
    app = FastAPI()
    app.middleware("http")(add_security_headers)

    @app.get("/json")
    def ordinary() -> JSONResponse:
        return JSONResponse({"ok": True}, headers={"Cache-Control": "private, max-age=30"})

    response = TestClient(app).get("/json")
    assert response.headers["Cache-Control"] == "private, max-age=30"
    assert "X-Accel-Buffering" not in response.headers
