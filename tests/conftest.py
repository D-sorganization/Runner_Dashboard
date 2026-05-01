import sys  # noqa: E402
from pathlib import Path  # noqa: E402

backend_dir = str(Path(__file__).parent.parent.resolve() / "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

REPO_ROOT = Path(__file__).parent.parent

import pytest  # noqa: E402


def _make_principal(principal_id: str, role: str):
    """Factory: build a Principal with a single role for use in tests."""
    from identity import Principal  # noqa: PLC0415

    return Principal(id=principal_id, type="bot", name=f"Test {role.capitalize()}", roles=[role])


def make_principal(role: str, principal_id: str | None = None):
    """Public factory consumed by parametrised tests.

    Usage::

        @pytest.mark.parametrize("role", ["admin", "operator", "viewer"])
        def test_xxx(role):
            p = make_principal(role)
            ...
    """
    pid = principal_id or f"test-{role}"
    return _make_principal(pid, role)


@pytest.fixture
def admin_principal():
    """Pre-built admin Principal for opt-in use in tests."""
    return _make_principal("test-admin", "admin")


@pytest.fixture
def operator_principal():
    """Pre-built operator Principal for opt-in use in tests."""
    return _make_principal("test-operator", "operator")


@pytest.fixture
def viewer_principal():
    """Pre-built viewer Principal for opt-in use in tests."""
    return _make_principal("test-viewer", "viewer")


@pytest.fixture
def make_authed_client():
    """Factory returning a FastAPI TestClient with the given Principal injected.

    Usage::

        def test_xxx(make_authed_client, admin_principal):
            client = make_authed_client(admin_principal)
            resp = client.get("/api/some-route")
            assert resp.status_code == 200
    """
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from identity import require_principal  # noqa: PLC0415
    from server import app  # noqa: PLC0415

    def _make(principal):
        app.dependency_overrides[require_principal] = lambda: principal
        return TestClient(app, raise_server_exceptions=False)

    yield _make
    app.dependency_overrides.clear()


@pytest.fixture
def mock_auth():
    """Opt-in fixture: override require_principal with a permanent admin Principal.

    Tests that want the old "bypass auth" behaviour must declare this fixture
    explicitly.  It is **not** autouse — authorization is exercised by default.
    """
    from identity import Principal, require_principal  # noqa: PLC0415
    from server import app  # noqa: PLC0415

    def _mock_principal():
        return Principal(id="test-admin", type="bot", name="Test Admin", roles=["admin"])

    app.dependency_overrides[require_principal] = _mock_principal
    yield
    app.dependency_overrides.clear()
