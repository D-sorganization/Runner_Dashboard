"""Tests for log_requests middleware path-filter logic (issue #413).

Verifies:
  - Errors (4xx/5xx) are always logged regardless of filter list.
  - High-volume filtered paths are sampled (not fully suppressed).
  - /api/system and /api/repos are NOT in the default filter list.
  - dashboard_config.LOG_FILTER_PATHS is configurable via env var.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


# ---------------------------------------------------------------------------
# dashboard_config.LOG_FILTER_PATHS
# ---------------------------------------------------------------------------


def test_log_filter_paths_default_excludes_system(monkeypatch) -> None:
    """Default LOG_FILTER_PATHS must NOT contain /api/system."""
    env = {k: v for k, v in os.environ.items() if k != "LOG_FILTER_PATHS"}
    monkeypatch.setattr(os, "environ", env)
    import dashboard_config  # noqa: PLC0415

    importlib.reload(dashboard_config)
    assert not any(p == "/api/system" or "/api/system".startswith(p) for p in dashboard_config.LOG_FILTER_PATHS), (
        "/api/system must NOT be in the default LOG_FILTER_PATHS"
    )


def test_log_filter_paths_default_excludes_repos(monkeypatch) -> None:
    """Default LOG_FILTER_PATHS must NOT contain /api/repos."""
    env = {k: v for k, v in os.environ.items() if k != "LOG_FILTER_PATHS"}
    monkeypatch.setattr(os, "environ", env)
    import dashboard_config  # noqa: PLC0415

    importlib.reload(dashboard_config)
    assert not any(p == "/api/repos" or "/api/repos".startswith(p) for p in dashboard_config.LOG_FILTER_PATHS), (
        "/api/repos must NOT be in the default LOG_FILTER_PATHS"
    )


def test_log_filter_paths_configurable_via_env(monkeypatch) -> None:
    """LOG_FILTER_PATHS can be overridden via environment variable."""
    env = os.environ.copy()
    env["LOG_FILTER_PATHS"] = "/api/custom,/api/other"
    monkeypatch.setattr(os, "environ", env)
    import dashboard_config  # noqa: PLC0415

    importlib.reload(dashboard_config)
    assert "/api/custom" in dashboard_config.LOG_FILTER_PATHS
    assert "/api/other" in dashboard_config.LOG_FILTER_PATHS


def test_log_filter_paths_is_tuple(monkeypatch) -> None:
    """LOG_FILTER_PATHS must be a tuple so str.startswith() works directly."""
    env = {k: v for k, v in os.environ.items() if k != "LOG_FILTER_PATHS"}
    monkeypatch.setattr(os, "environ", env)
    import dashboard_config  # noqa: PLC0415

    importlib.reload(dashboard_config)
    assert isinstance(dashboard_config.LOG_FILTER_PATHS, tuple)


# ---------------------------------------------------------------------------
# log_requests middleware behaviour (unit-level, no network)
# ---------------------------------------------------------------------------


def _make_request(path: str) -> MagicMock:
    """Build a minimal Request mock for the given path."""
    req = MagicMock()
    req.url.path = path
    req.method = "GET"
    return req


def _make_response(status_code: int = 200) -> MagicMock:
    """Build a minimal Response mock with the given status code."""
    resp = MagicMock()
    resp.status_code = status_code
    return resp


@pytest.mark.asyncio
async def test_error_always_logged_even_on_filtered_path() -> None:
    """4xx/5xx responses on filtered paths must always be logged."""
    import dashboard_config  # noqa: PLC0415

    importlib.reload(dashboard_config)

    # Pick a path that is in the filter list.
    filtered_path = dashboard_config.LOG_FILTER_PATHS[0]
    request = _make_request(filtered_path)
    response = _make_response(status_code=500)

    call_next = AsyncMock(return_value=response)

    # Import server after backend is on path.
    import server  # noqa: PLC0415

    with patch.object(server.log, "info") as mock_log:
        # Force random to 0.99 so the sampling branch would suppress — but the
        # error branch should override it.
        with patch("server.random.random", return_value=0.99):
            await server.log_requests(request, call_next)

    mock_log.assert_called_once()
    args = mock_log.call_args[0]
    assert str(filtered_path) in args or filtered_path in str(args)


@pytest.mark.asyncio
async def test_200_on_filtered_path_suppressed_when_random_high() -> None:
    """200 responses on filtered paths are suppressed when random() >= 0.1."""
    import dashboard_config  # noqa: PLC0415

    importlib.reload(dashboard_config)

    filtered_path = dashboard_config.LOG_FILTER_PATHS[0]
    request = _make_request(filtered_path)
    response = _make_response(status_code=200)

    call_next = AsyncMock(return_value=response)

    import server  # noqa: PLC0415

    with patch.object(server.log, "info") as mock_log:
        with patch("server.random.random", return_value=0.99):  # above threshold
            await server.log_requests(request, call_next)

    mock_log.assert_not_called()


@pytest.mark.asyncio
async def test_200_on_filtered_path_logged_when_random_low() -> None:
    """200 responses on filtered paths are logged when random() < 0.1 (sampled)."""
    import dashboard_config  # noqa: PLC0415

    importlib.reload(dashboard_config)

    filtered_path = dashboard_config.LOG_FILTER_PATHS[0]
    request = _make_request(filtered_path)
    response = _make_response(status_code=200)

    call_next = AsyncMock(return_value=response)

    import server  # noqa: PLC0415

    with patch.object(server.log, "info") as mock_log:
        with patch("server.random.random", return_value=0.05):  # below threshold
            await server.log_requests(request, call_next)

    mock_log.assert_called_once()


@pytest.mark.asyncio
async def test_unfiltered_path_always_logged() -> None:
    """Requests to paths NOT in LOG_FILTER_PATHS are always logged (200 OK)."""
    import server  # noqa: PLC0415

    request = _make_request("/api/system")
    response = _make_response(status_code=200)

    call_next = AsyncMock(return_value=response)

    with patch.object(server.log, "info") as mock_log:
        with patch("server.random.random", return_value=0.99):  # would suppress filtered paths
            await server.log_requests(request, call_next)

    mock_log.assert_called_once()


@pytest.mark.asyncio
async def test_repos_path_always_logged() -> None:
    """/api/repos must be logged (not silently dropped) for incident review."""
    import server  # noqa: PLC0415

    request = _make_request("/api/repos")
    response = _make_response(status_code=200)

    call_next = AsyncMock(return_value=response)

    with patch.object(server.log, "info") as mock_log:
        with patch("server.random.random", return_value=0.99):
            await server.log_requests(request, call_next)

    mock_log.assert_called_once()


# ---------------------------------------------------------------------------
# Source-level assertions
# ---------------------------------------------------------------------------


def test_server_imports_dashboard_config() -> None:
    """server.py must import dashboard_config to use LOG_FILTER_PATHS."""
    server_src = (_BACKEND_DIR / "server.py").read_text(encoding="utf-8")
    assert "import dashboard_config" in server_src


def test_server_references_log_filter_paths() -> None:
    """server.py must reference dashboard_config.LOG_FILTER_PATHS in middleware."""
    server_src = (_BACKEND_DIR / "server.py").read_text(encoding="utf-8")
    assert "dashboard_config.LOG_FILTER_PATHS" in server_src


def test_server_does_not_suppress_system_or_repos() -> None:
    """The old hard-coded skip list must not suppress /api/system or /api/repos."""
    server_src = (_BACKEND_DIR / "server.py").read_text(encoding="utf-8")
    # These paths must not appear in a skip/filter tuple in the middleware.
    # The old list hard-coded them — confirm they're gone.
    assert '"/api/system"' not in server_src or "skip" not in server_src, (
        "/api/system found in a skip-list context in server.py"
    )
    assert '"/api/repos"' not in server_src or "skip" not in server_src, (
        "/api/repos found in a skip-list context in server.py"
    )


def test_dashboard_config_has_log_filter_paths() -> None:
    """dashboard_config must expose LOG_FILTER_PATHS constant."""
    import dashboard_config  # noqa: PLC0415

    assert hasattr(dashboard_config, "LOG_FILTER_PATHS"), "dashboard_config must define LOG_FILTER_PATHS"
    assert isinstance(dashboard_config.LOG_FILTER_PATHS, tuple)
