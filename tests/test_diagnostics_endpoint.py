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
        "integrations",
    }
    assert expected.issubset(diag_payload.keys()), f"missing keys: {expected - diag_payload.keys()}"


def test_diagnostics_machine_registry_block(diag_payload) -> None:
    reg = diag_payload["machine_registry"]
    assert "loaded" in reg
    assert "path" in reg
    # Either machines count is present (loaded) or error is present (failed)
    assert ("machines" in reg) or ("error" in reg)


def test_diagnostics_registry_path_matches_the_actual_loader_resolver(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#3380: diagnostics must report the same file load_machine_registry()
    actually reads, not a re-derived guess that can disagree with it.

    load_machine_registry() only accepts paths under its own module dir or
    ``~/.config/runner-dashboard`` (issue #355 path-escape guard), so this
    points HOME at a fake home directory rather than using tmp_path directly.
    """
    import machine_registry
    import server

    monkeypatch.setenv("HOME", str(tmp_path))
    config_dir = tmp_path / ".config" / "runner-dashboard"
    config_dir.mkdir(parents=True)
    local = config_dir / "machine_registry.yml"
    local.write_text("version: 1\nmachines: []\n", encoding="utf-8")
    monkeypatch.delenv("MACHINE_REGISTRY_PATH", raising=False)
    monkeypatch.setattr(machine_registry, "LOCAL_REGISTRY_PATH", local)

    payload = server._diagnostics_payload()
    reg = payload["machine_registry"]
    assert reg["path"] == str(local)
    assert reg["source"] == "local"
    assert reg["machines"] == 0


def test_diagnostics_registry_path_reflects_env_override(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import server

    monkeypatch.setenv("HOME", str(tmp_path))
    config_dir = tmp_path / ".config" / "runner-dashboard"
    config_dir.mkdir(parents=True)
    custom = config_dir / "custom.yml"
    custom.write_text("version: 1\nmachines: [{name: m1}]\n", encoding="utf-8")
    monkeypatch.setenv("MACHINE_REGISTRY_PATH", str(custom))

    payload = server._diagnostics_payload()
    reg = payload["machine_registry"]
    assert reg["path"] == str(custom)
    assert reg["source"] == "env"
    assert reg["machines"] == 1


def test_diagnostics_registry_path_falls_back_to_shipped(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import machine_registry
    import server

    monkeypatch.delenv("MACHINE_REGISTRY_PATH", raising=False)
    monkeypatch.setattr(machine_registry, "LOCAL_REGISTRY_PATH", tmp_path / "missing.yml")

    payload = server._diagnostics_payload()
    reg = payload["machine_registry"]
    assert reg["path"] == str(machine_registry.DEFAULT_REGISTRY_PATH)
    assert reg["source"] == "shipped"


def test_diagnostics_registry_path_reported_on_load_error(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The loaded:false branch must report the same resolved path too."""
    import machine_registry
    import server

    monkeypatch.setenv("HOME", str(tmp_path))
    config_dir = tmp_path / ".config" / "runner-dashboard"
    config_dir.mkdir(parents=True)
    local = config_dir / "machine_registry.yml"
    local.write_text("not: [valid, yaml, {{{", encoding="utf-8")
    monkeypatch.delenv("MACHINE_REGISTRY_PATH", raising=False)
    monkeypatch.setattr(machine_registry, "LOCAL_REGISTRY_PATH", local)

    payload = server._diagnostics_payload()
    reg = payload["machine_registry"]
    assert reg["loaded"] is False
    assert reg["path"] == str(local)
    assert reg["source"] == "local"


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


def test_diagnostics_integrations_block_reports_repository_management(diag_payload) -> None:
    """The standalone/no-fleet profile (#3180) needs to see which optional
    integrations are missing without the endpoint ever erroring for it."""
    integrations = diag_payload["integrations"]
    assert "error" not in integrations, f"integration_health raised: {integrations}"
    rm_status = integrations["repository_management"]
    assert "available" in rm_status
    assert "reason" in rm_status
    if not rm_status["available"]:
        assert rm_status["reason"]
