"""Release artifact must ship backend's repo-root scripts/ imports (#3660)."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_SCRIPT = REPO_ROOT / "deploy" / "package-dashboard-artifact.sh"
SMOKE_SCRIPT = REPO_ROOT / "deploy" / "artifact_backend_import_smoke.py"


def _load_smoke():
    spec = importlib.util.spec_from_file_location("artifact_backend_import_smoke", SMOKE_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load_smoke()


def test_packager_stages_full_scripts_tree() -> None:
    content = PACKAGE_SCRIPT.read_text(encoding="utf-8")
    assert 'rsync -a --exclude=' in content and '"${SCRIPT_DIR}/scripts/" "${STAGE_DIR}/scripts/"' in content
    assert "claude_statusline_quota.py" not in content.split("# 5b.")[1].split("# 6.")[0]


def test_backend_scripts_import_modules_exist_in_repo() -> None:
    required = smoke._backend_scripts_imports(REPO_ROOT / "backend")
    assert "scripts.automerge_guard" in required
    missing = [name.replace(".", "/") + ".py" for name in required if not (REPO_ROOT / name.replace(".", "/")).with_suffix(".py").exists()]
    # Path: scripts.automerge_guard -> scripts/automerge_guard.py
    missing = []
    for name in required:
        rel = Path(*name.split(".")).with_suffix(".py")
        if not (REPO_ROOT / rel).is_file():
            missing.append(str(rel))
    assert not missing, f"repo missing scripts modules imported by backend: {missing}"


def _stage_minimal_artifact(stage: Path, *, include_automerge: bool = True) -> None:
    """Stage backend/ + scripts/ the way the packager does (without npm/wheels)."""
    stage.mkdir(parents=True, exist_ok=True)
    for sub in ("backend", "scripts"):
        dest = stage / sub
        src = REPO_ROOT / sub
        if shutil.which("rsync"):
            subprocess.run(
                [
                    "rsync",
                    "-a",
                    "--exclude=__pycache__",
                    "--exclude=*.pyc",
                    "--exclude=.pytest_cache",
                    f"{src}/",
                    f"{dest}/",
                ],
                check=True,
            )
        else:
            shutil.copytree(src, dest, dirs_exist_ok=True)
    if not include_automerge:
        (stage / "scripts" / "automerge_guard.py").unlink(missing_ok=True)


def test_extracted_tarball_passes_scripts_import_smoke(tmp_path: Path) -> None:
    """Build a tarball from staged backend+scripts, extract, run the smoke importer."""
    stage = tmp_path / "stage"
    _stage_minimal_artifact(stage)
    tarball = tmp_path / "artifact.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        for item in stage.iterdir():
            tar.add(item, arcname=item.name)

    extract = tmp_path / "extract"
    extract.mkdir()
    with tarfile.open(tarball, "r:gz") as tar:
        tar.extractall(extract)

    env = {**os.environ, "ARTIFACT_ROOT": str(extract)}
    proc = subprocess.run(
        [sys.executable, str(SMOKE_SCRIPT), "--files-only"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_extracted_tarball_fails_when_scripts_module_missing(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    _stage_minimal_artifact(stage, include_automerge=False)
    tarball = tmp_path / "artifact.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        for item in stage.iterdir():
            tar.add(item, arcname=item.name)
    extract = tmp_path / "extract"
    extract.mkdir()
    with tarfile.open(tarball, "r:gz") as tar:
        tar.extractall(extract)

    env = {**os.environ, "ARTIFACT_ROOT": str(extract)}
    proc = subprocess.run(
        [sys.executable, str(SMOKE_SCRIPT), "--files-only"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode != 0
    assert "automerge_guard" in proc.stderr + proc.stdout


def test_packager_invokes_backend_scripts_smoke_after_install() -> None:
    content = PACKAGE_SCRIPT.read_text(encoding="utf-8")
    assert "artifact_backend_import_smoke.py" in content
    assert content.index("install-dashboard-artifact.sh") < content.index("artifact_backend_import_smoke.py")
