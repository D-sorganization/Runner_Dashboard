# ---------------------------------------------------------------------------
# Fleet Testing Standards §5 — env vars MUST be set before heavy imports.
# See Repository_Management/docs/FLEET_TESTING_STANDARDS.md.
# ---------------------------------------------------------------------------
import os  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

# Backend singletons resolve their state directories during test collection.
# Give the whole process an isolated directory under the normal allowed config
# root before any backend import can read or mutate operator-owned state.
_test_config_root = Path.home() / ".config" / "runner-dashboard" / ".test-runs"
_test_config_root.mkdir(parents=True, exist_ok=True)
_test_config_dir = tempfile.TemporaryDirectory(prefix="pytest-", dir=_test_config_root, ignore_cleanup_errors=True)
os.environ["RUNNER_DASHBOARD_CONFIG_DIR"] = _test_config_dir.name

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
os.environ.setdefault("STAFF_MOCK_INSTALLED", "1")

# Strip git hook environment variables so tests creating temporary git repos are isolated
for _var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX"):
    os.environ.pop(_var, None)
os.environ["PRE_COMMIT_ALLOW_NO_CONFIG"] = "1"

# matplotlib headless backend, set before any matplotlib import.
os.environ.setdefault("MPLBACKEND", "Agg")

# Qt headless backend, for repos that import PyQt/PySide indirectly.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys  # noqa: E402

REPO_ROOT = Path(__file__).parent.parent.resolve()
backend_dir = str(REPO_ROOT / "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

repo_root_str = str(REPO_ROOT)
if repo_root_str not in sys.path:
    sys.path.insert(0, repo_root_str)

# Fleet API clients (#1228) are stdlib scripts, imported by module name in tests/clients.
clients_dir = str(REPO_ROOT / "clients" / "fleet")
if clients_dir not in sys.path:
    sys.path.insert(0, clients_dir)

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


# Every GitHub credential source the backend can use (#1528): the httpx client
# reads the token and GitHub App env, and the ``gh`` CLI fallback reads its login
# from ``GH_CONFIG_DIR``.
_GITHUB_CREDENTIAL_ENV = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_APP_ID",
    "GITHUB_APP_INSTALLATION_ID",
    "GITHUB_APP_PRIVATE_KEY",
    "GITHUB_APP_PRIVATE_KEY_FILE",
)


@pytest.fixture(autouse=True)
def _no_real_github_credentials(tmp_path, monkeypatch):
    """Tests never hold a real GitHub credential (#1528).

    With a developer's token or ``gh auth`` login visible, code-request tests
    created real issues via ``gh_utils.gh_api_write``. Every credential env var is
    removed, the ``gh`` CLI gets an empty per-test config dir, and ``gh_client``'s
    cached token is cleared. A test that needs a token sets a fake one itself.
    """
    try:
        import gh_client  # noqa: PLC0415
    except ImportError:
        gh_client = None  # noqa: N816

    for name in _GITHUB_CREDENTIAL_ENV:
        monkeypatch.delenv(name, raising=False)
    gh_config = tmp_path / "gh-config"
    gh_config.mkdir(exist_ok=True)
    monkeypatch.setenv("GH_CONFIG_DIR", str(gh_config))
    if gh_client is not None:
        monkeypatch.setattr(gh_client, "_cached_token", None)
        monkeypatch.setattr(gh_client, "_cached_token_expires_at", 0.0)


@pytest.fixture(autouse=True)
def _reset_main_cache_between_tests():
    """Clear the shared backend cache before and after every test."""
    from cache_utils import cache_clear  # noqa: PLC0415

    cache_clear()
    yield
    cache_clear()


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


@pytest.fixture(autouse=True)
def _hermetic_staff_workspace(tmp_path, monkeypatch):
    """Isolate staff.workspace so tests never touch a real checkout (#1521).

    ``staff.workspace.repos_roots()`` used to always append the developer's
    real ``~/Repositories`` (and friends) after any configured
    ``STAFF_REPOS_ROOT``, so a staff test that submitted a run could
    discover a real checkout and run real ``git worktree add`` / ``gh``
    against it. This fixture neutralizes discovery for every test: only a
    test's own ``STAFF_REPOS_ROOT`` is resolved, never the defaults, and
    worktree/RM-root paths live under this test's own ``tmp_path``.

    A test that genuinely needs a checkout must build a fake one under
    ``tmp_path`` and monkeypatch the relevant ``staff.workspace`` function
    itself (several already do this, e.g. ``find_repo_checkout`` /
    ``add_worktree`` in ``tests/api/test_staff_consolidation.py``) — that
    per-test monkeypatch simply overrides the default set up here.
    """
    try:
        from staff import knowledge_refresh as knowledge_refresh_mod  # noqa: PLC0415
    except ImportError:
        knowledge_refresh_mod = None
    from staff import workspace as workspace_mod  # noqa: PLC0415

    def configured_roots_only() -> list[Path]:
        # A test's own STAFF_REPOS_ROOT (a tmp_path corpus) is honoured; the
        # developer's real ~/Repositories defaults never are.
        configured = os.environ.get("STAFF_REPOS_ROOT", "")
        return [Path(p) for p in configured.split(os.pathsep) if p and Path(p).is_dir()]

    monkeypatch.setattr(workspace_mod, "repos_roots", configured_roots_only)
    # knowledge_refresh binds repos_roots at import time; patch that name too.
    if knowledge_refresh_mod is not None:
        monkeypatch.setattr(knowledge_refresh_mod, "repos_roots", configured_roots_only)
    monkeypatch.setenv("STAFF_WORKTREES_ROOT", str(tmp_path / "staff-worktrees"))
    monkeypatch.setenv("STAFF_RM_ROOT", str(tmp_path / "staff-rm-root"))

    real_add_worktree = workspace_mod.add_worktree

    def _guarded_add_worktree(checkout, worktree, branch):
        # DbC guard: fail loudly instead of silently shelling out to real
        # git if a test-created run ever resolves a worktree path outside
        # this test's own tmp_path.
        try:
            Path(worktree).resolve().relative_to(tmp_path.resolve())
        except ValueError:
            raise AssertionError(
                f"add_worktree() target {worktree!r} is outside the pytest tmp_path "
                f"{tmp_path!r}; staff tests must never create real git worktrees (#1521)."
            ) from None
        return real_add_worktree(checkout, worktree, branch)

    monkeypatch.setattr(workspace_mod, "add_worktree", _guarded_add_worktree)


@pytest.fixture(scope="session", autouse=True)
def _cleanup_stores_session_teardown():
    yield
    try:
        from staff.store import reset_store  # noqa: PLC0415

        reset_store()
    except Exception:
        pass
    try:
        from staff.audit import reset_audit_store  # noqa: PLC0415

        reset_audit_store()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _staff_verification_off(monkeypatch):
    """Finished test runs never ask GitHub for their PR (#1516); verification tests opt in."""
    monkeypatch.setenv("STAFF_VERIFY_MODE", "off")
