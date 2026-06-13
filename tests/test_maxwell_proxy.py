"""Contract tests for Maxwell-Daemon proxy routes (rd#102)."""

from __future__ import annotations  # noqa: E402

import os  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

# Ensure backend/ is on sys.path before importing the app
_BACKEND = Path(__file__).parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")


@pytest.fixture(scope="module")
def app():
    """Import and return the FastAPI app (module-scoped to pay import cost once)."""
    import server  # noqa: PLC0415

    return server.app


@pytest_asyncio.fixture
async def client(app, mock_auth):  # noqa: ARG001
    """Async HTTP client wired directly to the ASGI app."""
    from httpx import ASGITransport, AsyncClient  # noqa: PLC0415

    headers = {
        "Authorization": "Bearer test-key",
        "X-Requested-With": "XMLHttpRequest",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers=headers,
    ) as ac:
        yield ac


def _mock_httpx_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """Build a mock httpx.Response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.json.return_value = json_data
    return mock_resp


def test_maxwell_router_paths_are_mounted_once(app) -> None:
    """The canonical Maxwell APIRouter is mounted and not shadowed by server.py routes."""
    expected_paths = {
        "/api/maxwell/status",
        "/api/maxwell/control",
        "/api/maxwell/version",
        "/api/maxwell/daemon-status",
        "/api/maxwell/tasks",
        "/api/maxwell/tasks/{task_id}",
        "/api/maxwell/dispatch",
        "/api/maxwell/chat",
        "/api/maxwell/pipeline-control/{action}",
        "/api/maxwell/backends",
        "/api/maxwell/workers",
        "/api/maxwell/cost",
        "/api/maxwell/pipeline-state",
    }
    maxwell_routes = [route for route in app.routes if getattr(route, "path", "").startswith("/api/maxwell/")]
    route_paths = [route.path for route in maxwell_routes]

    for path in expected_paths:
        assert route_paths.count(path) == 1, f"{path} route count was {route_paths.count(path)}"

    assert all(route.endpoint.__module__ == "routers.maxwell" for route in maxwell_routes)


def _make_mock_client(get_return=None, post_return=None, get_side_effect=None, post_side_effect=None):
    """Build a mock AsyncClient context manager that yields a mock client."""
    mock_client = MagicMock()
    if get_side_effect is not None:
        mock_client.get = AsyncMock(side_effect=get_side_effect)
    elif get_return is not None:
        mock_client.get = AsyncMock(return_value=get_return)
    if post_side_effect is not None:
        mock_client.post = AsyncMock(side_effect=post_side_effect)
    elif post_return is not None:
        mock_client.post = AsyncMock(return_value=post_return)

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


# ─── GET /api/maxwell/version ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_maxwell_version_returns_200_with_contract(client) -> None:
    """GET /api/maxwell/version proxies daemon response through the contract model."""
    # Real MD /api/version shape is {daemon, contract} (#956).
    payload = {"daemon": "1.0.0", "contract": "2.0.0"}
    mock_cm = _make_mock_client(get_return=_mock_httpx_response(payload))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/version")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == "1.0.0"
    assert data["contract"] == "2.0.0"
    assert data["contract_compatible"] is True


@pytest.mark.asyncio
async def test_get_maxwell_version_daemon_unreachable_returns_503(client) -> None:
    """When daemon is unreachable, the mounted router returns a proxy error."""
    mock_cm = _make_mock_client(get_side_effect=httpx.ConnectError("connection refused"))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/version")
    assert resp.status_code == 503
    data = resp.json()
    assert data["detail"] == "maxwell connection error"


# ─── GET /api/maxwell/tasks ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_maxwell_tasks_returns_200_with_tasks_key(client) -> None:
    """GET /api/maxwell/tasks proxies daemon response and exposes 'tasks' key."""
    payload = {"tasks": [], "total": 0}
    mock_cm = _make_mock_client(get_return=_mock_httpx_response(payload))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/tasks")
    assert resp.status_code == 200
    data = resp.json()
    assert "tasks" in data


@pytest.mark.asyncio
async def test_get_maxwell_tasks_daemon_unreachable_returns_503(client) -> None:
    """When daemon is unreachable, the mounted router returns a proxy error."""
    mock_cm = _make_mock_client(get_side_effect=httpx.ConnectError("connection refused"))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/tasks")
    assert resp.status_code == 503
    data = resp.json()
    assert data["detail"] == "maxwell connection error"


# ─── GET /api/maxwell/tasks/{task_id} ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_maxwell_task_detail_returns_200(client) -> None:
    """GET /api/maxwell/tasks/{task_id} proxies daemon task detail."""
    payload = {"id": "abc123", "status": "completed", "repo": "my-repo"}
    mock_cm = _make_mock_client(get_return=_mock_httpx_response(payload))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/tasks/abc123")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("id") == "abc123"


@pytest.mark.asyncio
async def test_get_maxwell_task_detail_daemon_unreachable_returns_503(client) -> None:
    """When daemon is unreachable, the mounted router returns a proxy error."""
    mock_cm = _make_mock_client(get_side_effect=httpx.ConnectError("connection refused"))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/tasks/abc123")
    assert resp.status_code == 503
    data = resp.json()
    assert data["detail"] == "maxwell connection error"


# ─── GET /api/maxwell/daemon-status ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_maxwell_daemon_status_returns_200(client) -> None:
    """GET /api/maxwell/daemon-status maps pipeline_state→state and merges v2 counts (#955)."""

    # The route fetches /api/status then /api/v2/status; dispatch by URL.
    async def _get_by_url(url, *_a, **_kw):
        if url.endswith("/api/v2/status"):
            return _mock_httpx_response({"counts": {"running": 2, "queued": 3, "completed": 7, "failed": 1}})
        return _mock_httpx_response(
            {"pipeline_state": "running", "active_task_id": "t1", "gate": "open", "sandbox": "enabled"}
        )

    mock_cm = _make_mock_client(get_side_effect=_get_by_url)
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/daemon-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "running"
    assert data["active_tasks"] == 2
    assert data["queued_tasks"] == 3
    assert data["completed_tasks"] == 7


@pytest.mark.asyncio
async def test_get_maxwell_daemon_status_unreachable_returns_503(client) -> None:
    """When daemon is unreachable, the mounted router returns a proxy error."""
    mock_cm = _make_mock_client(get_side_effect=httpx.ConnectError("connection refused"))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/daemon-status")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "maxwell connection error"


# ─── POST /api/maxwell/pipeline-control/{action} ─────────────────────────────


@pytest.mark.asyncio
async def test_maxwell_pipeline_control_pause_returns_200(client) -> None:
    """POST /api/maxwell/pipeline-control/pause returns 200 when daemon responds."""
    payload = {"action": "pause", "status": "paused"}
    mock_cm = _make_mock_client(post_return=_mock_httpx_response(payload))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post("/api/maxwell/pipeline-control/pause", json={"confirmation_token": "test-token"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_maxwell_pipeline_control_resume_returns_200(client) -> None:
    """POST /api/maxwell/pipeline-control/resume returns 200 when daemon responds."""
    payload = {"action": "resume", "status": "resumed"}
    mock_cm = _make_mock_client(post_return=_mock_httpx_response(payload))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post("/api/maxwell/pipeline-control/resume", json={"confirmation_token": "test-token"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_maxwell_pipeline_control_badaction_returns_422(client) -> None:
    """POST /api/maxwell/pipeline-control/badaction must return 422."""
    resp = await client.post("/api/maxwell/pipeline-control/badaction", json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_maxwell_pipeline_control_forwards_caller_token(client, monkeypatch) -> None:
    """Pipeline-control forwards the caller confirmation token without injection."""
    monkeypatch.setattr("routers.maxwell.MAXWELL_API_TOKEN", "test-maxwell-token")
    mock_cm = _make_mock_client(post_return=_mock_httpx_response({"action": "abort", "status": "aborted"}))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post(
            "/api/maxwell/pipeline-control/abort",
            json={"confirmation_token": "caller-token"},
        )
    assert resp.status_code == 200
    sent_body = mock_cm.__aenter__.return_value.post.call_args.kwargs["content"]
    sent_headers = mock_cm.__aenter__.return_value.post.call_args.kwargs["headers"]
    assert '"confirmation_token": "caller-token"' in sent_body
    assert sent_headers["Authorization"] == "Bearer test-maxwell-token"


@pytest.mark.asyncio
async def test_maxwell_pipeline_control_daemon_unreachable_returns_503(client) -> None:
    """When daemon is unreachable, the mounted router returns a proxy error."""
    mock_cm = _make_mock_client(post_side_effect=httpx.ConnectError("connection refused"))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post("/api/maxwell/pipeline-control/abort", json={"confirmation_token": "test-token"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "maxwell connection error"


@pytest.mark.asyncio
async def test_maxwell_pipeline_control_proxies_to_non_v1_path(client) -> None:
    """Issue #952: the proxy must target MD's real route POST /api/control/{action},
    NOT the nonexistent /api/v1/control/{action} (which 404'd every control)."""
    payload = {"action": "pause", "applied_at": "2026-06-12T00:00:00Z", "previous_state": "running"}
    mock_cm = _make_mock_client(post_return=_mock_httpx_response(payload))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post(
            "/api/maxwell/pipeline-control/pause",
            json={"confirmation_token": "test-token"},
        )
    assert resp.status_code == 200
    called_url = mock_cm.__aenter__.return_value.post.call_args.args[0]
    assert called_url.endswith("/api/control/pause"), f"expected /api/control/pause, got {called_url}"
    assert "/api/v1/control/" not in called_url


@pytest.mark.asyncio
async def test_maxwell_pipeline_control_returns_md_response_shape(client) -> None:
    """Issue #952: the proxy surfaces MD's {action, applied_at, previous_state}
    instead of silently defaulting to {action, status:'ok'}."""
    payload = {"action": "abort", "applied_at": "2026-06-12T01:02:03Z", "previous_state": "paused"}
    mock_cm = _make_mock_client(post_return=_mock_httpx_response(payload))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post(
            "/api/maxwell/pipeline-control/abort",
            json={"confirmation_token": "test-token"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "abort"
    assert data["applied_at"] == "2026-06-12T01:02:03Z"
    assert data["previous_state"] == "paused"


# ─── POST /api/maxwell/dispatch ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_maxwell_backends_accepts_daemon_string_list_shape(client) -> None:
    """Issue #954: MD returns list[str] from /api/v1/backends; RD must normalize it."""
    mock_cm = _make_mock_client(get_return=_mock_httpx_response({"backends": ["openai", "ollama"]}))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/backends")
    assert resp.status_code == 200
    assert resp.json()["backends"] == [
        {"name": "openai", "type": "unknown", "enabled": True, "model": None, "status": None},
        {"name": "ollama", "type": "unknown", "enabled": True, "model": None, "status": None},
    ]


@pytest.mark.asyncio
async def test_maxwell_dispatch_daemon_unreachable_returns_503(client) -> None:
    """When daemon is unreachable, the mounted router returns a proxy error."""
    mock_cm = _make_mock_client(post_side_effect=httpx.ConnectError("connection refused"))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post(
            "/api/maxwell/dispatch",
            json={"repo": "test-repo", "confirmation_token": "test-token", "prompt": "do a thing"},
        )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "maxwell connection error"


@pytest.mark.asyncio
async def test_maxwell_dispatch_posts_to_confirmation_gated_endpoint(client) -> None:
    """Issue #953: dispatch must target MD's /api/dispatch (the gated, idempotent
    endpoint), sending the exact DispatchRequest contract body — not /api/v1/tasks,
    which silently dropped confirmation_token + idempotency_key."""
    payload = {"task_id": "t-99", "status": "queued", "queued_at": "2026-06-12T00:00:00Z"}
    mock_cm = _make_mock_client(post_return=_mock_httpx_response(payload, status_code=202))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post(
            "/api/maxwell/dispatch",
            json={
                "repo": "test-repo",
                "confirmation_token": "secret",
                "prompt": "build the thing",
                "idempotency_key": "idem-1",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "t-99"
    assert data["queued_at"] == "2026-06-12T00:00:00Z"

    post = mock_cm.__aenter__.return_value.post
    called_url = post.call_args.args[0] if post.call_args.args else post.call_args.kwargs.get("url", "")
    assert called_url.endswith("/api/dispatch")
    # Exact contract body: confirmation_token, prompt, repo, idempotency_key only.
    import json as _json  # noqa: PLC0415

    sent = _json.loads(post.call_args.kwargs["content"])
    assert sent["confirmation_token"] == "secret"
    assert sent["prompt"] == "build the thing"
    assert sent["repo"] == "test-repo"
    assert sent["idempotency_key"] == "idem-1"


@pytest.mark.asyncio
async def test_maxwell_dispatch_preserves_caller_idempotency_key(client) -> None:
    """Issue #953: a caller-supplied idempotency_key is forwarded verbatim so
    retries with the same key do not create duplicate daemon-side tasks."""
    mock_cm = _make_mock_client(post_return=_mock_httpx_response({"task_id": "t-1", "status": "queued"}, 202))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        await client.post(
            "/api/maxwell/dispatch",
            json={"confirmation_token": "tok", "prompt": "p", "idempotency_key": "stable-key"},
        )
    import json as _json  # noqa: PLC0415

    sent = _json.loads(mock_cm.__aenter__.return_value.post.call_args.kwargs["content"])
    assert sent["idempotency_key"] == "stable-key"


@pytest.mark.asyncio
async def test_maxwell_dispatch_surfaces_daemon_confirmation_rejection(client) -> None:
    """Issue #953 acceptance: an invalid confirmation token is rejected by the
    daemon (403), and the proxy surfaces that rejection rather than masking it
    as a 200 success."""
    mock_cm = _make_mock_client(
        post_return=_mock_httpx_response({"detail": "invalid confirmation_token"}, status_code=403)
    )
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post(
            "/api/maxwell/dispatch",
            json={"confirmation_token": "wrong", "prompt": "p"},
        )
    assert resp.status_code == 403
    assert "invalid confirmation token" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_maxwell_dispatch_requires_prompt(client) -> None:
    """Issue #953: a dispatch missing ``prompt`` is rejected at the RD boundary
    with a clear 422, not an opaque daemon-side error."""
    resp = await client.post(
        "/api/maxwell/dispatch",
        json={"confirmation_token": "tok"},
    )
    assert resp.status_code == 422
    assert "prompt" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_maxwell_dispatch_idempotency_conflict_is_409(client) -> None:
    """Issue #953: a daemon-side idempotency conflict surfaces as a 409, not a
    duplicate-creating success."""
    mock_cm = _make_mock_client(post_return=_mock_httpx_response({"detail": "duplicate"}, status_code=409))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post(
            "/api/maxwell/dispatch",
            json={"confirmation_token": "tok", "prompt": "p", "idempotency_key": "dup"},
        )
    assert resp.status_code == 409


# ─── #959 status surface + #963 lifecycle platform guard ─────────────────────


@pytest.mark.asyncio
async def test_status_surfaces_configured_and_lifecycle_flags(client) -> None:
    """Issue #959/#963: /status reports `configured` and `lifecycle_supported`."""
    import routers.maxwell as mx  # noqa: PLC0415

    # Daemon unreachable + no systemd → stopped, lifecycle unsupported.
    mock_cm = _make_mock_client(get_side_effect=httpx.ConnectError("refused"))
    with (
        patch("httpx.AsyncClient", return_value=mock_cm),
        patch.object(mx, "_lifecycle_supported", return_value=False),
    ):
        resp = await client.get("/api/maxwell/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "configured" in data
    assert data["lifecycle_supported"] is False
    assert data["service_detail"] == "systemd lifecycle control unavailable on this platform"


@pytest.mark.asyncio
async def test_control_returns_501_when_lifecycle_unsupported(client) -> None:
    """Issue #963: start/stop must report unsupported, not silently no-op, off-systemd."""
    import routers.maxwell as mx  # noqa: PLC0415

    with patch.object(mx, "_lifecycle_supported", return_value=False):
        resp = await client.post(
            "/api/maxwell/control",
            json={"action": "start", "approved_by": "tester"},
        )
    assert resp.status_code == 501
    assert "unavailable on this platform" in resp.json()["detail"]


# ─── #961 tasks proxy keys pagination as next_cursor ─────────────────────────


@pytest.mark.asyncio
async def test_tasks_proxy_exposes_next_cursor_not_cursor(client) -> None:
    """Issue #961: the tasks list response is keyed next_cursor, never cursor."""
    payload = {
        "tasks": [{"id": "t-1", "status": "running", "created_at": "2026-06-12T00:00:00Z"}],
        "next_cursor": None,
        "total": 1,
    }
    mock_cm = _make_mock_client(get_return=_mock_httpx_response(payload))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/maxwell/tasks")
    assert resp.status_code == 200
    data = resp.json()
    assert "next_cursor" in data
    assert "cursor" not in data
    assert data["tasks"][0]["status"] == "running"


# ─── POST /api/maxwell/chat ──────────────────────────────────────────────────


def _chat_post_cm(json_data: dict | None = None, status_code: int = 200, post_side_effect=None) -> MagicMock:
    """Build a mock AsyncClient whose .post() returns an MD ChatResponse JSON body.

    Issue #957: the chat proxy now POSTs to MD's request/response ``/api/chat``
    (not a streamed endpoint) and emits ``ChatResponse.content``.
    """
    mock_client = MagicMock()
    if post_side_effect is not None:
        mock_client.post = AsyncMock(side_effect=post_side_effect)
    else:
        mock_client.post = AsyncMock(return_value=_mock_httpx_response(json_data or {}, status_code))
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


def _md_chat_response(content: str) -> dict:
    """A minimal MD ``ChatResponse`` payload."""
    return {
        "content": content,
        "backend": "openai",
        "backend_name": "OpenAI",
        "model": "gpt-x",
        "finish_reason": "stop",
        "route_reason": "default",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


@pytest.mark.asyncio
async def test_maxwell_chat_daemon_unreachable_streams_fallback(client) -> None:
    """When daemon is unreachable, chat streams a readable fallback instead of breaking the tab."""
    mock_cm = _chat_post_cm(post_side_effect=httpx.ConnectError("connection refused"))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post("/api/maxwell/chat", json={"message": "status"})
    assert resp.status_code == 200
    assert "Maxwell-Daemon is unreachable" in resp.text


@pytest.mark.asyncio
async def test_maxwell_chat_renders_answer_text_not_raw_json(client) -> None:
    """Issue #957: the proxy emits ChatResponse.content, not the JSON serialization."""
    mock_cm = _chat_post_cm(_md_chat_response("The queue is handled in queue_cleanup.py."))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post("/api/maxwell/chat", json={"message": "where is the queue?"})
    assert resp.status_code == 200
    assert resp.text == "The queue is handled in queue_cleanup.py."
    assert "backend_name" not in resp.text  # not the raw JSON
    assert "{" not in resp.text


@pytest.mark.asyncio
async def test_maxwell_chat_sends_messages_not_history_or_stream(client) -> None:
    """Issue #957: MD rejects history/stream (extra=forbid); proxy must send messages[]."""
    mock_cm = _chat_post_cm(_md_chat_response("ok"))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post(
            "/api/maxwell/chat",
            json={
                "message": "and now?",
                "history": [
                    {"id": 1, "role": "operator", "content": "first question"},
                    {"id": 2, "role": "maxwell", "content": "first answer"},
                ],
            },
        )
    assert resp.status_code == 200
    sent = mock_cm.__aenter__.return_value.post.call_args.kwargs["json"]
    assert "history" not in sent
    assert "stream" not in sent
    # Roles are mapped operator->user, maxwell->assistant (MD ChatMessage roles).
    assert sent["messages"] == [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
    ]


@pytest.mark.asyncio
async def test_maxwell_chat_forwards_repo_and_repo_root(client) -> None:
    """Codebase Q&A scoping fields (issue #838) are forwarded to the codebase route."""
    mock_cm = _chat_post_cm(_md_chat_response("where queue is handled"))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post(
            "/api/maxwell/chat",
            json={
                "message": "where is /api/queue handled?",
                "repo": "Runner_Dashboard",
                "repo_root": "/home/runner/Runner_Dashboard",
            },
        )
    assert resp.status_code == 200
    sent = mock_cm.__aenter__.return_value.post.call_args.kwargs["json"]
    assert sent["repo"] == "Runner_Dashboard"
    assert sent["repo_root"] == "/home/runner/Runner_Dashboard"
    # Codebase-scoped chat routes to MD's dedicated /api/chat/codebase endpoint.
    called_url = mock_cm.__aenter__.return_value.post.call_args.args[0]
    assert called_url.endswith("/api/chat/codebase")


@pytest.mark.asyncio
async def test_maxwell_chat_omits_repo_fields_when_absent(client) -> None:
    """Fleet-status chat keeps an unchanged payload (no repo/repo_root keys) on /api/chat."""
    mock_cm = _chat_post_cm(_md_chat_response("ok"))
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post("/api/maxwell/chat", json={"message": "status"})
    assert resp.status_code == 200
    sent = mock_cm.__aenter__.return_value.post.call_args.kwargs["json"]
    assert "repo" not in sent
    assert "repo_root" not in sent
    called_url = mock_cm.__aenter__.return_value.post.call_args.args[0]
    assert called_url.endswith("/api/chat")


@pytest.mark.asyncio
async def test_maxwell_chat_501_degrades_gracefully_for_codebase(client) -> None:
    """A reachable daemon without codebase support (Maxwell_Daemon#948) returns 501;
    the proxy degrades it into a clear, actionable message, not a raw HTTP code."""
    mock_cm = _chat_post_cm({}, status_code=501)
    with patch("httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post(
            "/api/maxwell/chat",
            json={"message": "where is X?", "repo": "Runner_Dashboard"},
        )
    assert resp.status_code == 200
    assert "Codebase Q&A is not available" in resp.text
    assert "HTTP 501" not in resp.text
