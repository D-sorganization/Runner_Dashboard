"""Keep artifact dependency locks and startup imports in sync."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_artifact_lock_contains_declared_library_runtime_dependencies() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = {
        dependency.partition("==")[0].lower().replace("_", "-"): dependency.partition("==")[2]
        for dependency in project["project"]["dependencies"]
    }
    lock = (ROOT / "requirements.lock.txt").read_text(encoding="utf-8")

    for package in ("pypdf", "sqlite-vec"):
        version = declared[package]
        block = re.search(
            rf"(?ms)^{re.escape(package)}=={re.escape(version)} \\\n(.*?)(?=^[A-Za-z0-9_.-]+==|\Z)",
            lock,
        )
        assert block is not None, f"{package}=={version} is absent from the artifact lock"
        assert "--hash=sha256:" in block.group(1), f"{package} lock entry must stay hash-pinned"


def test_artifact_preflight_imports_library_ingest_before_mutating_live_tree() -> None:
    installer = (ROOT / "deploy" / "install-dashboard-artifact.sh").read_text(encoding="utf-8")
    smoke_check = re.search(r"(?ms)^runtime_import_check\(\) \{(.*?)^\}", installer)

    assert smoke_check is not None
    assert '"${backend_dir}/library/ingest.py"' in smoke_check.group(1)
    assert 'import_check+=", library.ingest"' in smoke_check.group(1)
    preflight = installer.index('build_runtime_venv "${RUNTIME_PYTHON}" "${tmpdir}/preflight-venv"')
    first_live_mutation = installer.index('mkdir -p "${DEPLOY_DIR}"')
    assert 'runtime_import_check "${venv_dir}/bin/python" "${source_dir}/backend"' in installer
    assert 'runtime_import_check "${DEPLOY_DIR}/.venv/bin/python" "${DEPLOY_DIR}/backend"' in installer
    assert preflight < first_live_mutation
