"""Recorded main check-run fixtures for auto-release gate (2026-10-22 main)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

from main_auto_release.gate import commit_passes_main_ci_gate  # noqa: E402

_GREEN_SHA = "a474ee662a55f9437677116a4d269f74946a8d10"  # pragma: allowlist secret
_FAIL_SHA = "87fa0117aacefbf7b02ca72d52d207599fedd305"  # pragma: allowlist secret
_CANCEL_SHA = "3ee37e4a41514213222cb3730d44e189e3b81a15"  # pragma: allowlist secret


def _runs(path: Path, sha: str) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [{**run, "head_sha": sha} for run in payload["check_runs"]]


def test_recorded_a474ee66_is_ci_green() -> None:
    runs = _runs(REPO / "tests/deploy/fixtures/a474ee6_check_runs.json", _GREEN_SHA)
    ok, detail = commit_passes_main_ci_gate(runs, _GREEN_SHA)
    assert ok is True
    assert _GREEN_SHA[:7] in detail


def test_recorded_87fa0117_must_not_release() -> None:
    runs = _runs(REPO / "tests/deploy/fixtures/87fa011_check_runs.json", _FAIL_SHA)
    ok, detail = commit_passes_main_ci_gate(runs, _FAIL_SHA)
    assert ok is False
    assert "Vitest" in detail or "failed" in detail


def test_recorded_3ee37e4a_must_not_release() -> None:
    runs = _runs(REPO / "tests/deploy/fixtures/3ee37e4_check_runs.json", _CANCEL_SHA)
    ok, detail = commit_passes_main_ci_gate(runs, _CANCEL_SHA)
    assert ok is False
    assert "cancelled" in detail.lower() or "failed" in detail
