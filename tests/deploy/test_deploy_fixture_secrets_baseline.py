"""Ensure deploy CI-gate fixtures stay audited in `.secrets.baseline` (issue #396)."""

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
_EXPECTED_HASH = "82cc4f92bc8a9943b925686738602c2b362185b0"


def _normalize_results(results: dict) -> dict:
    normalized: dict = {}
    for filename, findings in results.items():
        key = str(PurePosixPath(filename.replace("\\", "/")))
        normalized.setdefault(key, []).extend(findings)
        for finding in normalized[key]:
            if isinstance(finding, dict) and "filename" in finding:
                finding["filename"] = str(PurePosixPath(finding["filename"].replace("\\", "/")))
    return normalized


def test_ecb3f6b_fixture_listed_in_secrets_baseline() -> None:
    payload = json.loads(_BASELINE.read_text(encoding="utf-8"))
    findings = payload.get("results", {}).get(_ECB3F6B_FIXTURE)
    assert findings, f"{_ECB3F6B_FIXTURE} must be audited in .secrets.baseline"
    assert any(f.get("hashed_secret") == _EXPECTED_HASH for f in findings)


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
