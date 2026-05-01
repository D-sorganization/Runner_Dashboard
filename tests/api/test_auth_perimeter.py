"""Auth perimeter tests (issue #343).

Verifies that:
- Unauthenticated non-loopback requests are rejected with 401 by the auth gate.
  (TestClient sends from 127.0.0.1 which is covered by the loopback admin bypass
  from issue #315.  We test the logic directly via require_principal with a spoofed
  non-loopback request rather than via the HTTP stack, to avoid coupling these tests
  to #315 fix status.)
- Operator-scoped principal is rejected on admin-only routes with 403.
- Admin-scoped principal is accepted on admin-only routes.
- make_principal / make_authed_client factories work across roles.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("DASHBOARD_API_KEY", "test-key")

import server  # noqa: E402
from conftest import make_principal  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from identity import require_principal  # noqa: E402

# ─── Helpers ─────────────────────────────────────────────────────────────────


def _authed_client(role: str) -> TestClient:
    """Create a TestClient with the given role injected."""
    principal = make_principal(role)
    server.app.dependency_overrides[require_principal] = lambda: principal
    return TestClient(server.app, raise_server_exceptions=False)


# ─── 1. Unauthenticated → 401 (tested via require_principal unit test) ────────
#
# The HTTP-level test cannot easily test 401 because TestClient connects from
# 127.0.0.1, which is granted automatic admin access by the loopback bypass
# (issue #315).  We instead unit-test require_principal directly with a spoofed
# non-loopback request that has no token or session.


def test_require_principal_raises_401_for_remote_unauthenticated_request() -> None:
    """require_principal must raise HTTP 401 for non-loopback requests with no creds."""
    mock_request = MagicMock()
    mock_request.client.host = "192.168.1.100"  # non-loopback
    mock_request.headers.get.return_value = None  # no impersonation header
    mock_request.state = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        require_principal(request=mock_request, header_token=None, cookie_token=None)

    assert exc_info.value.status_code == 401
    assert "Authentication required" in str(exc_info.value.detail)


@pytest.fixture(autouse=True)
def _restore_dependency_overrides():
    """Ensure overrides set by the local ``_authed_client`` helper are cleaned up.

    The ``make_authed_client`` conftest fixture handles its own teardown separately.
    """
    yield
    server.app.dependency_overrides.clear()


# ─── 2. Operator on admin route → 403 ────────────────────────────────────────

ADMIN_ONLY_ROUTES = [
    ("GET", "/api/admin/principals"),
    ("GET", "/api/admin/tokens"),
    ("POST", "/api/admin/principals/test-id/token"),
]


@pytest.mark.parametrize(("method", "path"), ADMIN_ONLY_ROUTES)
def test_operator_on_admin_route_returns_403(method: str, path: str) -> None:
    """Operator-scoped principal must be rejected on admin-only routes with 403."""
    client = _authed_client("operator")
    resp = client.request(method, path, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 403, f"Expected 403 on {method} {path} with operator, got {resp.status_code}"


@pytest.mark.parametrize(("method", "path"), ADMIN_ONLY_ROUTES)
def test_viewer_on_admin_route_returns_403(method: str, path: str) -> None:
    """Viewer-scoped principal must be rejected on admin-only routes with 403."""
    client = _authed_client("viewer")
    resp = client.request(method, path, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 403, f"Expected 403 on {method} {path} with viewer, got {resp.status_code}"


# ─── 3. Admin accepted on admin routes ───────────────────────────────────────


def test_admin_principal_can_list_principals() -> None:
    """Admin-scoped principal must be accepted on GET /api/admin/principals."""
    client = _authed_client("admin")
    resp = client.get("/api/admin/principals", headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200


def test_admin_principal_can_list_tokens() -> None:
    """Admin-scoped principal must be accepted on GET /api/admin/tokens."""
    client = _authed_client("admin")
    resp = client.get("/api/admin/tokens", headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200


# ─── 4. make_principal factory ───────────────────────────────────────────────

@pytest.mark.parametrize("role", ["admin", "operator", "viewer"])
def test_make_principal_factory_produces_correct_role(role: str) -> None:
    """make_principal factory must produce a Principal with the given role."""
    p = make_principal(role)
    assert role in p.roles
    assert p.id == f"test-{role}"
    assert p.type == "bot"


# ─── 5. make_authed_client fixture ───────────────────────────────────────────


def test_make_authed_client_admin_can_access_protected_route() -> None:
    """make_authed_client (inline) with admin principal must get 200 on /api/admin/principals."""
    client = _authed_client("admin")
    resp = client.get("/api/admin/principals", headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200


def test_make_authed_client_viewer_blocked_from_admin_route() -> None:
    """make_authed_client (inline) with viewer principal must get 403 on admin routes."""
    client = _authed_client("viewer")
    resp = client.get("/api/admin/principals", headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 403


def test_conftest_make_authed_client_works(make_authed_client) -> None:
    """Verify the conftest make_authed_client factory works with different roles."""
    admin_p = make_principal("admin")
    viewer_p = make_principal("viewer")

    admin_client = make_authed_client(admin_p)
    resp = admin_client.get("/api/admin/principals", headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200, f"admin should get 200 on /api/admin/principals, got {resp.status_code}"

    # Reset for viewer test
    server.app.dependency_overrides.clear()
    viewer_client = make_authed_client(viewer_p)
    resp = viewer_client.get("/api/admin/principals", headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 403, f"viewer should get 403 on /api/admin/principals, got {resp.status_code}"
