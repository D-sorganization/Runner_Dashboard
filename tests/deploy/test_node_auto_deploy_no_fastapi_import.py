"""Regression: node auto-deploy helpers must not import FastAPI (#3666 deploy smoke)."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "deploy"

_IMPORT_SMOKE = textwrap.dedent(
    """
    import importlib.abc
    import sys
    import tempfile
    from pathlib import Path

    class BlockFastAPI(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path, target=None):
            if fullname == "fastapi" or fullname.startswith("fastapi."):
                raise ModuleNotFoundError("No module named 'fastapi'")
            return None

    sys.meta_path.insert(0, BlockFastAPI())
    sys.path.insert(0, "deploy")

    from node_auto_deploy.registry_migration import migrate_legacy_registry_to_local_config
    from node_auto_deploy.activity import verify_staff_activity_probe, staff_database_path
    from node_auto_deploy.transaction import copy_mutable_state

    live = Path(tempfile.mkdtemp()) / "dashboard"
    live.mkdir()
    (live / "backend").mkdir()
    (live / "backend" / "machine_registry.yml").write_text("machines: []\\n", encoding="utf-8")
    candidate = live.parent / "dashboard-releases" / ("c" * 40)
    candidate.mkdir(parents=True)
    import os

    os.environ["HOME"] = str(live.parent)

    assert migrate_legacy_registry_to_local_config(deploy_backend_dir=live / "backend") in {
        "migrated",
        "skipped_local_exists",
        "skipped_env_override",
        "skipped_no_legacy",
    }
    copy_mutable_state(live, candidate)
    try:
        verify_staff_activity_probe(
            "http://127.0.0.1:8321",
            "probe-token",
            staff_database_path({"STAFF_RUNS_DB": str(live / "missing.sqlite3")}),
        )
    except Exception as exc:
        if "fastapi" in str(exc).lower():
            raise
    assert "fastapi" not in sys.modules
    print("ok")
    """
)


def test_deploy_post_restart_helpers_import_without_fastapi() -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _IMPORT_SMOKE],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "ok" in proc.stdout


def test_registry_migration_copies_legacy_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sys.path.insert(0, str(DEPLOY))
    from node_auto_deploy.registry_migration import migrate_legacy_registry_to_local_config  # noqa: PLC0415

    monkeypatch.delenv("MACHINE_REGISTRY_PATH", raising=False)
    local = tmp_path / ".config" / "runner-dashboard" / "machine_registry.yml"
    monkeypatch.setenv("HOME", str(tmp_path))
    deploy_backend = tmp_path / "live" / "backend"
    deploy_backend.mkdir(parents=True)
    (deploy_backend / "machine_registry.yml").write_text("machines: []\n", encoding="utf-8")

    outcome = migrate_legacy_registry_to_local_config(deploy_backend_dir=deploy_backend)
    assert outcome == "migrated"
    assert local.is_file()
    assert local.read_text(encoding="utf-8") == "machines: []\n"
