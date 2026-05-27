# ---------------------------------------------------------------------------
# Fleet Testing Standards §5 — env vars MUST be set before heavy imports.
# See Repository_Management/docs/FLEET_TESTING_STANDARDS.md.
# ---------------------------------------------------------------------------
import os  # noqa: E402

# C-extension thread safety. Many "xdist worker crashed" failures
# come from MKL/OpenBLAS forking under xdist. Pin to single-threaded
# for tests; production code can re-thread itself if it needs to.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

# Disable autoderiving fleet nodes in tests to prevent network timeouts
os.environ["AUTODERIVE_FLEET_NODES"] = "0"
os.environ["FLEET_NODES"] = ""

# matplotlib headless backend, set before any matplotlib import.
os.environ.setdefault("MPLBACKEND", "Agg")

# Qt headless backend, for repos that import PyQt/PySide indirectly.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

backend_dir = str(Path(__file__).parent.parent.resolve() / "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

REPO_ROOT = Path(__file__).parent.parent

import pytest  # noqa: E402


# ---------------------------------------------------------------------------
# Fleet Testing Standards §5 — block real outbound HTTP in the unit lane.
# This dashboard backend uses httpx + requests heavily; unit tests must mock.
# Tests that genuinely need network must be marked `requires_network` (or
# `integration` / `network`, which are already excluded from the default lane).
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _no_real_network_in_unit_lane(request, monkeypatch):
    """Block real outbound HTTP from unit tests by default."""
    if "unit" not in request.keywords:
        return
    if any(m in request.keywords for m in ("requires_network", "network", "integration", "e2e")):
        return

    def _refuse(*_a, **_kw):
        raise RuntimeError(
            "Unit test made a real network call. Mock with `respx` or "
            "`pytest-httpx`, or mark the test "
            "`@pytest.mark.requires_network`."
        )

    for module in ("httpx", "requests", "urllib.request"):
        try:
            mod = __import__(module, fromlist=["*"])
            for attr in ("get", "post", "put", "delete", "request"):
                if hasattr(mod, attr):
                    monkeypatch.setattr(mod, attr, _refuse, raising=False)
        except ImportError:
            pass


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
