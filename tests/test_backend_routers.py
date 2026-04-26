"""Structural tests for the extracted backend routers (issue #4).

These are import-time and shape tests — they verify the routers are correctly
structured and expose the expected routes without needing a running server.
"""

from __future__ import annotations  # noqa: E402

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

_py311 = pytest.mark.skipif(sys.version_info < (3, 11), reason="dispatch_contract requires Python 3.11+")


# ---------------------------------------------------------------------------
# Dispatch router
# ---------------------------------------------------------------------------


@_py311
def test_dispatch_router_importable() -> None:
    from routers import dispatch

    assert hasattr(dispatch, "router"), "routers/dispatch.py must export 'router'"


@_py311
def test_dispatch_router_has_expected_routes() -> None:
    from routers import dispatch

    paths = {r.path for r in dispatch.router.routes}  # type: ignore[attr-defined]
    assert "/api/fleet/dispatch/actions" in paths
    assert "/api/fleet/dispatch/validate" in paths
    assert "/api/fleet/dispatch/submit" in paths


def test_dispatch_router_uses_dispatch_contract() -> None:
    """The dispatch router module must reference dispatch_contract — its core dependency."""
    source = (_BACKEND_DIR / "routers" / "dispatch.py").read_text(encoding="utf-8")
    assert "import dispatch_contract" in source, "dispatch router must import dispatch_contract"


# ---------------------------------------------------------------------------
# Credentials router
# ---------------------------------------------------------------------------


def test_credentials_router_importable() -> None:
    from routers import credentials  # noqa: PLC0415

    assert hasattr(credentials, "router"), "routers/credentials.py must export 'router'"


def test_credentials_router_has_credentials_route() -> None:
    from routers import credentials  # noqa: PLC0415

    paths = {r.path for r in credentials.router.routes}  # type: ignore[attr-defined]
    assert "/api/credentials" in paths


def test_credentials_router_no_secret_exposure() -> None:
    """Credentials router must never log or return raw env var values."""
    source = (_BACKEND_DIR / "routers" / "credentials.py").read_text(encoding="utf-8")
    assert "os.environ.get" in source, "must probe env vars"
    # Ensure the router doesn't return raw env values — only booleans
    assert "return os.environ" not in source, "must not return raw env var contents"


# ---------------------------------------------------------------------------
# server.py registration
# ---------------------------------------------------------------------------


def test_server_registers_dispatch_router() -> None:
    """server.py must include the dispatch router (not re-implement it inline)."""
    server_src = (_BACKEND_DIR / "server.py").read_text(encoding="utf-8")
    assert "from routers import dispatch" in server_src or "routers.dispatch" in server_src
    assert "include_router(_dispatch_router.router)" in server_src


def test_server_registers_credentials_router() -> None:
    """server.py must include the credentials router (not re-implement it inline)."""
    server_src = (_BACKEND_DIR / "server.py").read_text(encoding="utf-8")
    assert "from routers import credentials" in server_src or "routers.credentials" in server_src
    assert "include_router(_credentials_router.router)" in server_src


def test_server_dispatch_not_inline() -> None:
    """The dispatch endpoints must not be defined inline in server.py."""
    server_src = (_BACKEND_DIR / "server.py").read_text(encoding="utf-8")
    assert '@app.get("/api/fleet/dispatch/actions")' not in server_src
    assert '@app.post("/api/fleet/dispatch/validate")' not in server_src
    assert '@app.post("/api/fleet/dispatch/submit")' not in server_src


def test_server_credentials_not_inline() -> None:
    """The credentials endpoint must not be defined inline in server.py."""
    server_src = (_BACKEND_DIR / "server.py").read_text(encoding="utf-8")
    assert '@app.get("/api/credentials")' not in server_src
