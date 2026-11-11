"""Release artifact review finalization must not depend on checkout imports (#4359)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "deploy" / "artifact_backend_import_smoke.py"


def _extract_runtime(tmp_path: Path, *, shared: bool = True) -> Path:
    stage = tmp_path / "stage"
    stage.mkdir()
    packages = ["backend", "scripts", "config"] + (["shared_scripts"] if shared else [])
    for package in packages:
        shutil.copytree(ROOT / package, stage / package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    archive = tmp_path / "runtime.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for entry in stage.iterdir():
            tar.add(entry, arcname=entry.name)
    extracted = tmp_path / "installed"
    extracted.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(extracted, filter="data")
    return extracted


def _env(root: Path) -> dict[str, str]:
    # Do not pass live credentials or the checkout import path into reviewed code.
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(
            part in key.upper() for part in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "PYTHONPATH", "GITHUB_APP")
        )
    }
    env.update(
        ARTIFACT_ROOT=str(root),
        AUTODERIVE_FLEET_NODES="0",
        FLEET_NODES="",
        RUNNER_DASHBOARD_CONFIG_DIR=str(root / "test-config"),
        SESSION_SECRET=os.urandom(32).hex(),
    )
    return env


def test_packagers_include_shared_runtime_package() -> None:
    packager = (ROOT / "deploy" / "package-dashboard-artifact.sh").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert '"${SCRIPT_DIR}/shared_scripts/" "${STAGE_DIR}/shared_scripts/"' in packager
    assert "COPY --chown=appuser:appuser shared_scripts/ ./shared_scripts/" in dockerfile


def test_smoke_rejects_extracted_artifact_without_shared_runtime(tmp_path: Path) -> None:
    root = _extract_runtime(tmp_path, shared=False)
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(SMOKE), "--files-only"],
        cwd=root,
        env=_env(root),
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert "shared_scripts" in result.stdout + result.stderr


def test_smoke_accepts_shared_package_and_rejects_missing_module(tmp_path: Path) -> None:
    root = _extract_runtime(tmp_path)
    args = [sys.executable, "-I", "-B", str(SMOKE), "--files-only"]
    good = subprocess.run(args, cwd=root, env=_env(root), text=True, capture_output=True, timeout=120)
    assert good.returncode == 0, good.stdout + good.stderr
    (root / "shared_scripts" / "independent_exact_head_review.py").unlink()
    bad = subprocess.run(args, cwd=root, env=_env(root), text=True, capture_output=True, timeout=120)
    assert bad.returncode != 0
    assert "shared_scripts/independent_exact_head_review.py" in bad.stdout + bad.stderr


@pytest.mark.parametrize("kind", ["legacy", "independent", "wrong-head", "wrong-pr"])
def test_extracted_runtime_finalizes_reviews_without_checkout(tmp_path: Path, kind: str) -> None:
    root = _extract_runtime(tmp_path)
    # -I ignores caller cwd/PYTHONPATH; only extracted runtime code is inserted.
    probe = r"""
import os, sys
from pathlib import Path
root = Path(os.environ["ARTIFACT_ROOT"]).resolve()
sys.path[:0] = [str(root / "backend"), str(root)]
from staff import review, independent_exact_head_verdict as verdict
from shared_scripts import independent_exact_head_review as shared
for module in (review, verdict, shared):
    assert Path(module.__file__).resolve().is_relative_to(root), module.__file__
head = "a" * 40
posted = []
verdict.fetch_pull_head_sha = lambda repo, pr: head
verdict.publish_independent_exact_head_verdict = lambda **kwargs: posted.append(kwargs)
kind = sys.argv[1]
line = "STAFF_RESULT: review approve #123"
if kind != "legacy":
    line = f"STAFF_RESULT: independent_review PASS #123 @{head}"
if kind == "wrong-head":
    line = line.replace(head, "b" * 40)
if kind == "wrong-pr":
    line = line.replace("#123", "#124")
result = review.finalize_code_reviewer_run(status="succeeded", failure_class="", retryable=False,
    error="", remediation="", result_line=line, same_provider=False, repo="example-repo",
    reviewer_model="independent-test-model", expected_pr_number=123)
if kind in ("wrong-head", "wrong-pr"):
    assert result.status == "failed", result
    assert result.failure_class == "invalid_output", result
    assert not posted
else:
    assert result.status == "succeeded", result
    assert len(posted) == (0 if kind == "legacy" else 1)
    if posted:
        assert posted[0]["head_sha"] == head and posted[0]["pr_number"] == 123
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", probe, kind],
        cwd=root,
        env=_env(root),
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_files_only_rejects_missing_backend(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(SMOKE), "--files-only"],
        cwd=tmp_path,
        env=_env(tmp_path),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "artifact missing backend" in result.stdout + result.stderr


def test_smoke_handles_package_import_and_lazy_shared_dependency(tmp_path: Path) -> None:
    for name in ("backend", "scripts", "shared_scripts"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "backend" / "consumer.py").write_text(
        "import shared_scripts\ndef finalize():\n    from shared_scripts import missing\n", encoding="utf-8"
    )
    args = [sys.executable, "-I", "-B", str(SMOKE), "--files-only"]
    result = subprocess.run(args, cwd=tmp_path, env=_env(tmp_path), text=True, capture_output=True, timeout=30)
    assert result.returncode != 0
    assert "shared_scripts/missing.py" in result.stdout + result.stderr
    (tmp_path / "shared_scripts" / "missing.py").write_text("", encoding="utf-8")
    result = subprocess.run(args, cwd=tmp_path, env=_env(tmp_path), text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
