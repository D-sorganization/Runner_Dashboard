"""Artifact install must preserve root-level runtime files (#2777).

``rsync -a --delete`` removes anything at the deploy root that is not in the
artifact unless an exclude matches it. Backend modules persist state under
``~/actions-runners/dashboard/``; every such file must be excluded, and every
directly-executed deploy script must carry the executable bit in the index.
"""

from __future__ import annotations

import fnmatch
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPO_ROOT / "deploy"
INSTALLER = DEPLOY / "install-dashboard-artifact.sh"
BACKEND = REPO_ROOT / "backend"

RUNTIME_FILES = (
    "code_requests.json",
    "agent_profiles.json",
    "feature_requests.json",
    "feature_requests.json.migrated",
    "prompt_notes.json",
    "prompt_templates.json",
    "code_request_plans.json",
    "orchestration_audit.json",
    "processed_envelopes.json",
)

# Scripts that are only ever ``source``d; every other deploy/**/*.sh is executed.
SOURCED_ONLY = {"deploy/lib.sh", "deploy/auto-deploy-env.sh"}

_DEPLOY_ROOT_PATH = re.compile(r'"actions-runners"\s*/\s*"dashboard"\s*/\s*"([^"/]+)"')


def _excludes() -> list[str]:
    return re.findall(r"--exclude='([^']+)'", INSTALLER.read_text(encoding="utf-8"))


def _is_excluded(name: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.startswith("/"):
            if pattern.count("/") == 1 and fnmatch.fnmatch(name, pattern[1:]):
                return True
        elif fnmatch.fnmatch(name, pattern):
            return True
    return False


@pytest.mark.parametrize("name", RUNTIME_FILES)
def test_runtime_file_is_excluded_from_rsync_delete(name: str) -> None:
    assert _is_excluded(name, _excludes()), f"{name} would be deleted by artifact install"


def test_every_backend_deploy_root_state_file_is_excluded() -> None:
    patterns = _excludes()
    found: set[str] = set()
    for source in BACKEND.rglob("*.py"):
        found.update(_DEPLOY_ROOT_PATH.findall(source.read_text(encoding="utf-8")))
    assert found, "scan found no deploy-root paths; regex is stale"
    missing = sorted(n for n in found if not _is_excluded(n, patterns))
    assert not missing, f"runtime files deleted by artifact install: {missing}"


@pytest.mark.skipif(shutil.which("rsync") is None or __import__("os").name == "nt", reason="rsync requires POSIX")
def test_rsync_preserves_runtime_files_and_deletes_stale_code(tmp_path: Path) -> None:
    source, deploy = tmp_path / "stage", tmp_path / "live"
    (source / "backend").mkdir(parents=True)
    (source / "backend" / "server.py").write_text("# new\n", encoding="utf-8")
    deploy.mkdir()
    (deploy / "stale_module.py").write_text("remove me\n", encoding="utf-8")
    for name in RUNTIME_FILES:
        (deploy / name).write_text(name, encoding="utf-8")
    command = ["rsync", "-a", "--delete"]
    for pattern in _excludes():
        command.extend(("--exclude", pattern))
    command.extend((f"{source}/", f"{deploy}/"))
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert not (deploy / "stale_module.py").exists()
    for name in RUNTIME_FILES:
        assert (deploy / name).read_text(encoding="utf-8") == name


def _index_modes() -> dict[str, str]:
    out = subprocess.run(
        ["git", "ls-files", "-s", "deploy"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    modes: dict[str, str] = {}
    for line in out.splitlines():
        meta, path = line.split("\t", 1)
        modes[path] = meta.split()[0]
    return modes


def test_executed_deploy_scripts_are_mode_755_in_index() -> None:
    bad = []
    for path, mode in _index_modes().items():
        if not path.endswith(".sh") or path in SOURCED_ONLY:
            continue
        first = (REPO_ROOT / path).read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
        assert first.startswith("#!"), f"{path} lacks a shebang; add it to SOURCED_ONLY if it is sourced"
        if mode != "100755":
            bad.append(f"{path} ({mode})")
    assert not bad, f"executed deploy scripts not 100755: {bad}"
