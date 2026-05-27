"""Tests for /api/diagnostics — the operator-facing config-health endpoint.

The endpoint exists to surface failures that previously only appeared in
journald (registry load failures, follower-mode, empty FLEET_NODES). These
tests pin the schema so `deploy/deploy-check.sh` and external monitoring can
rely on it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make backend/ importable
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# server.py imports rely on POSIX file-mode checks (security.py guards
# against world-writable config). Windows-mounted filesystems can't
# represent those bits and trip the check during module init. The check
# itself is covered by security.py's own tests; here we patch it to a
# no-op so the diagnostics schema can be exercised on any host.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32" and not Path("/.dockerenv").exists(),
    reason=(
        "server.py module-import triggers security.py file-mode checks that "
        "fail on Windows-mounted filesystems; tests run on Linux CI."
    ),
)


@pytest.fixture
def diag_payload():
    """Build a diagnostics payload by calling the helper directly.

    This bypasses the FastAPI request lifecycle so the test doesn't need a
    full uvicorn app + TestClient. The route handler is a thin wrapper
    around _diagnostics_payload(); covering the helper is the high-value
    test.
    """
    import server

    return server._diagnostics_payload()


def test_diagnostics_schema_top_level_keys(diag_payload) -> None:
    """The schema is a deploy-check contract — adding fields is OK,
    removing/renaming breaks the post-deploy validator and any external
    monitors that grep for these keys."""
    expected = {
        "ok",
        "hostname",
        "timestamp",
        "machine_registry",
        "fleet_federation",
        "leader",
        "deployment",
        "cache",
    }
    assert expected.issubset(diag_payload.keys()), f"missing keys: {expected - diag_payload.keys()}"


def test_diagnostics_machine_registry_block(diag_payload) -> None:
    reg = diag_payload["machine_registry"]
    assert "loaded" in reg
    assert "path" in reg
    # Either machines count is present (loaded) or error is present (failed)
    assert ("machines" in reg) or ("error" in reg)


def test_diagnostics_fleet_federation_block(diag_payload) -> None:
    fleet = diag_payload["fleet_federation"]
    for key in ("source", "node_count", "nodes", "machine_role"):
        assert key in fleet, f"missing fleet_federation.{key}"
    assert fleet["source"] in {"env", "registry", "empty"}
    assert isinstance(fleet["node_count"], int)
    assert isinstance(fleet["nodes"], list)


def test_diagnostics_ok_field_reflects_registry_load(diag_payload) -> None:
    """The `ok` summary must reflect at minimum whether the registry loaded.

    deploy-check.sh greps for ok=true; if a registry-load failure didn't
    flip ok=false, the validator would pass on a silently-broken host.
    """
    reg_loaded = diag_payload["machine_registry"].get("loaded") is True
    if not reg_loaded:
        assert diag_payload["ok"] is False, "ok must be False when registry fails to load"


def test_diagnostics_deployment_block_has_server_py(diag_payload) -> None:
    """The deployment block reports key file mtimes so deploy-check.sh
    can detect a deploy that updated some files but not others.
    """
    deploy = diag_payload["deployment"]
    assert "server_py" in deploy
    # Either we found it (path + mtime + size) or it's missing — both are
    # reported.
    entry = deploy["server_py"]
    assert "path" in entry
    assert ("mtime" in entry and "size" in entry) or entry.get("missing") is True
