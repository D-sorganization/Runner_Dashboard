"""Ensure deploy CI-gate fixtures stay compatible with detect-secrets (issue #396)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from shutil import copy2

import pytest

REPO = Path(__file__).resolve().parents[2]
_BASELINE = REPO / ".secrets.baseline"
_ECB3F6B_FIXTURE = "tests/deploy/fixtures/ecb3f6b_check_runs.json"
_EXCLUDE = (
    r"(package-lock\.json|uv\.lock|\.tsbuildinfo$|node_modules/|\.venv/|"
    r"\.ci-ds-venv/|\.git/|\.secrets\.baseline)"
)


def _normalize_results(results: dict) -> dict:
    normalized: dict = {}
    for filename, findings in results.items():
        key = str(PurePosixPath(filename.replace("\\", "/")))
        normalized.setdefault(key, []).extend(findings)
        for finding in normalized[key]:
            if isinstance(finding, dict) and "filename" in finding:
                finding["filename"] = str(PurePosixPath(finding["filename"].replace("\\", "/")))
    return normalized


def test_ecb3f6b_fixture_scans_clean_without_baseline_row() -> None:
    pytest.importorskip("detect_secrets")
    fixture_path = REPO / _ECB3F6B_FIXTURE
    scan = json.loads(
        subprocess.check_output(
            [sys.executable, "-m", "detect_secrets", "scan", str(fixture_path)],
            text=True,
        )
    )
    assert scan.get("results") == {}
    baseline = json.loads(_BASELINE.read_text(encoding="utf-8")).get("results", {})
    assert _ECB3F6B_FIXTURE not in baseline


def test_detect_secrets_scan_does_not_expand_baseline_for_ecb3f6b_fixture() -> None:
    pytest.importorskip("detect_secrets")
    with tempfile.TemporaryDirectory() as tmp:
        baseline_copy = Path(tmp) / ".secrets.baseline"
        copy2(_BASELINE, baseline_copy)
        before = _normalize_results(json.loads(baseline_copy.read_text(encoding="utf-8"))["results"])

        subprocess.run(
            [
                sys.executable,
                "-m",
                "detect_secrets",
                "scan",
                "--baseline",
                str(baseline_copy),
                "--exclude-files",
                _EXCLUDE,
            ],
            cwd=REPO,
            check=True,
            capture_output=True,
            text=True,
        )

        after = _normalize_results(json.loads(baseline_copy.read_text(encoding="utf-8"))["results"])
    assert before == after
