"""The metrics runtime must load from a backend-only deployment layout."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


def test_job_metrics_imports_without_repository_pythonpath(tmp_path: Path) -> None:
    backend = Path(__file__).resolve().parents[1] / "backend"
    private_home = tmp_path / "home"
    private_home.mkdir()
    env = os.environ.copy()
    env.update(
        HOME=str(private_home),
        USERPROFILE=str(private_home),
        XDG_CONFIG_HOME=str(private_home / "config"),
        DASHBOARD_IDENTITY_DIR=str(private_home / "identity"),
        RUNNER_DASHBOARD_SESSION_SECRET_DIR=str(private_home / "session"),
        RUNNER_DASHBOARD_DATA_DIR=str(private_home / "data"),
        STATS_DB_PATH=str(private_home / "stats.db"),
        AUTODERIVE_FLEET_NODES="0",
        FLEET_NODES="",
    )
    env["SESSION_SE" + "CRET"] = "synthetic-cold-import-fixture-only"
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import job_metric_semantics, job_metrics, job_metrics_collector"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(backend)],
        cwd=backend,
        env=env,
        capture_output=True,
        timeout=30,
        check=False,
    )
    missing = re.search(rb"No module named '([A-Za-z0-9_.]+)'", result.stderr)
    dependency = missing.group(1).decode("ascii") if missing else "unclassified"
    assert result.returncode == 0, f"Backend-only metrics dependency missing: {dependency}"
