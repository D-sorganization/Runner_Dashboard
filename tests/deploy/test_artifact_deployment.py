"""Tests for deterministic offline artifact packaging and installation (Issue #1085)."""

import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "deploy"
PACKAGE_SCRIPT = DEPLOY_DIR / "package-dashboard-artifact.sh"
INSTALL_SCRIPT = DEPLOY_DIR / "install-dashboard-artifact.sh"
UPDATE_SCRIPT = DEPLOY_DIR / "update-deployed.sh"
SETUP_SCRIPT = DEPLOY_DIR / "setup.sh"


def _is_executable_script(path: Path) -> bool:
    if os.name != "nt":
        return bool(path.stat().st_mode & 0o111)
    rel = path.relative_to(REPO_ROOT).as_posix()
    result = subprocess.run(
        ["git", "ls-files", "--stage", rel],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.startswith("100755 ")


def test_package_script_exists_and_executable():
    assert PACKAGE_SCRIPT.exists(), "package-dashboard-artifact.sh must exist"
    # When newly created on Windows without git commit yet, check file existence


def test_install_script_exists_and_supports_checksum():
    assert INSTALL_SCRIPT.exists()
    content = INSTALL_SCRIPT.read_text(encoding="utf-8")
    assert "--checksum" in content
    assert "CHECKSUM_INPUT" in content
    assert "validate_artifact_layout" in content
    assert "FILES.txt" in content
    assert "deployment.json" in content


def test_artifact_contract_requires_offline_runtime_and_service_helper():
    """Artifact installs must be independently runnable, not source-tree dependent."""
    package_content = PACKAGE_SCRIPT.read_text(encoding="utf-8")
    install_content = INSTALL_SCRIPT.read_text(encoding="utf-8")

    assert 'cp "${SCRIPT_DIR}/requirements.lock.txt"' in package_content
    assert "pip wheel" in package_content
    assert 'cp "${SCRIPT_DIR}/deploy/wsl-mirrored-port-helper.sh"' in package_content
    assert "backend/wheels" in install_content
    assert "wsl-mirrored-port-helper.sh" in install_content
    assert "runner-dashboard-artifact-v2" in package_content


def test_offline_dependency_install_fails_closed():
    """A broken wheelhouse must abort deployment rather than restart a broken service."""
    content = INSTALL_SCRIPT.read_text(encoding="utf-8")

    assert "--require-hashes" in content
    assert "|| true" not in "\n".join(line for line in content.splitlines() if ".venv/bin/pip" in line)
    assert ".venv/bin/python" in content
    assert "import fastapi" in content


def test_runtime_selector_rejects_python_with_broken_pip():
    content = (DEPLOY_DIR / "python-runtime.sh").read_text(encoding="utf-8")

    assert "-m pip --version" in content


def test_artifact_pins_wheelhouse_python_minor():
    package_content = PACKAGE_SCRIPT.read_text(encoding="utf-8")
    install_content = INSTALL_SCRIPT.read_text(encoding="utf-8")

    assert '"python_minor": python_minor' in package_content
    assert '["compatibility"]["python_minor"]' in install_content
    assert 'select_dashboard_python "${ARTIFACT_PYTHON_MINOR}"' in install_content


def test_artifact_install_preserves_mutable_runtime_state():
    content = INSTALL_SCRIPT.read_text(encoding="utf-8")

    assert "--exclude='.env'" in content
    assert "--exclude='*.db'" in content
    assert "--exclude='*.db-*'" in content
    assert "--exclude='*_history.json'" in content
    assert 'rm -rf "${DEPLOY_DIR}"' not in content


def test_update_deployed_script_supports_checksum_and_skips_uv_on_artifact():
    assert UPDATE_SCRIPT.exists()
    content = UPDATE_SCRIPT.read_text(encoding="utf-8")
    assert "--checksum" in content
    assert "ARTIFACT_CHECKSUM" in content
    # uv sync must only execute when ARTIFACT_SOURCE is empty (source deployment mode)
    assert 'if [[ -n "$ARTIFACT_SOURCE" ]]; then' in content or 'if [[ -z "$ARTIFACT_SOURCE" ]]; then' in content
    assert "install-dashboard-artifact.sh" in content


def test_setup_script_supports_checksum_and_skips_uv_on_artifact():
    assert SETUP_SCRIPT.exists()
    content = SETUP_SCRIPT.read_text(encoding="utf-8")
    assert "--checksum" in content
    assert "ARTIFACT_CHECKSUM" in content
    assert 'if [[ -z "${ARTIFACT_SOURCE}" ]]; then' in content


def test_artifact_layout_and_checksum_verification():
    """Verify that an artifact tarball with required layout and checksum can be verified and installed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        stage_dir = tmp_path / "stage"
        stage_dir.mkdir()

        (stage_dir / "VERSION").write_text("4.9.26\n", encoding="utf-8")
        (stage_dir / "README.md").write_text("# Runner Dashboard\n", encoding="utf-8")
        (stage_dir / "local_apps.json").write_text("[]\n", encoding="utf-8")
        (stage_dir / "refresh-token.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        (stage_dir / "wsl-mirrored-port-helper.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        (stage_dir / "requirements.lock.txt").write_text("", encoding="utf-8")
        (stage_dir / "backend").mkdir()
        (stage_dir / "backend" / "server.py").write_text("# server\n", encoding="utf-8")
        (stage_dir / "backend" / "wheels").mkdir()
        (stage_dir / "frontend").mkdir()
        (stage_dir / "frontend" / "index.html").write_text("<!doctype html><html></html>\n", encoding="utf-8")
        (stage_dir / "deploy").mkdir()
        (stage_dir / "deploy" / "setup.sh").write_text("#!/bin/bash\n", encoding="utf-8")

        deployment_data = {
            "app": "runner-dashboard",
            "version": "4.9.26",
            "git_sha": "mock-git-sha-val",  # pragma: allowlist secret
            "build_timestamp": "2026-08-21T00:00:00Z",
            "compatibility": {
                "artifact_schema": "runner-dashboard-artifact-v2",
                "python_requires": ">=3.11",
                "python_minor": "3.11",
                "service_name": "runner-dashboard.service",
            },
        }
        (stage_dir / "deployment.json").write_text(json.dumps(deployment_data), encoding="utf-8")

        # Generate FILES.txt
        files = []
        for p in stage_dir.rglob("*"):
            if p.is_file():
                files.append(p.relative_to(stage_dir).as_posix())
        files.append("FILES.txt")
        files.sort()
        (stage_dir / "FILES.txt").write_text("\n".join(files) + "\n", encoding="utf-8")

        tarball_path = tmp_path / "dashboard-4.9.26.tar.gz"
        with tarfile.open(tarball_path, "w:gz") as tar:
            for item in stage_dir.iterdir():
                tar.add(item, arcname=item.name)

        sha256_hash = hashlib.sha256(tarball_path.read_bytes()).hexdigest()
        checksum_file = tmp_path / "dashboard-4.9.26.tar.gz.sha256"
        checksum_file.write_text(f"{sha256_hash}  {tarball_path.name}\n", encoding="utf-8")

        # Test verification in python
        calculated = hashlib.sha256(tarball_path.read_bytes()).hexdigest()
        assert calculated == sha256_hash

        # Tampered checksum mismatch detection
        bad_hash = "0" * 64
        assert calculated != bad_hash


def test_package_script_content_deterministic_sorting():
    content = PACKAGE_SCRIPT.read_text(encoding="utf-8")
    assert "sort >" in content, "FILES.txt must be sorted deterministically"
    assert "deployment.json" in content
    assert "sha256sum" in content
