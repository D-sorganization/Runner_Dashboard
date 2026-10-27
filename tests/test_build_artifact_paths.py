"""Packaging contract: local startup and containers serve the Vite build."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_start_script_uses_the_vite_output_directory() -> None:
    vite_config = (ROOT / "vite.config.ts").read_text()
    start_script = (ROOT / "start-dashboard.sh").read_text()

    assert re.search(r"root:\s*['\"]frontend['\"]", vite_config)
    assert re.search(r"outDir:\s*['\"]\.\./dist['\"]", vite_config)
    assert 'DIST_DIR="${SCRIPT_DIR}/dist"' in start_script


def test_docker_image_builds_and_copies_the_frontend() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert re.search(r"FROM\s+node:[^\n]+\s+AS\s+frontend-build", dockerfile)
    assert "RUN npm ci" in dockerfile
    assert "RUN npm run build" in dockerfile
    assert re.search(r"COPY --from=frontend-build(?:\s+--chown=[^\s]+)? /app/dist ./dist", dockerfile)


def test_backend_package_import_bootstraps_sibling_modules() -> None:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    result = subprocess.run(
        [sys.executable, "-c", "import backend.server"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
