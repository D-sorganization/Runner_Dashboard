"""Fail-closed CI gate semantics (Refs D-sorganization/Repository_Management#2883)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

from node_auto_deploy.check_runs import (  # noqa: E402
    SKIPPED_CONCLUSION_ALLOWLIST,
    check_name_passes,
)
from node_auto_deploy.ci_gate import ci_gate_for_commit  # noqa: E402

_SHA = "a" * 40


def test_skipped_alone_does_not_satisfy_check_name() -> None:
    assert not check_name_passes(
        [{"status": "completed", "conclusion": "skipped", "name": "guard"}],
    )


def test_duplicate_skipped_and_success_does_not_satisfy() -> None:
    assert not check_name_passes(
        [
            {"status": "completed", "conclusion": "skipped", "name": "lint"},
            {"status": "completed", "conclusion": "success", "name": "lint"},
        ],
    )


def test_cancelled_superseded_by_success_still_satisfies() -> None:
    assert check_name_passes(
        [
            {"status": "completed", "conclusion": "cancelled", "name": "docker-build-scan"},
            {"status": "completed", "conclusion": "success", "name": "docker-build-scan"},
        ],
    )


def test_ci_gate_rejects_skipped_only_payload() -> None:
    ok, detail = ci_gate_for_commit(
        [{"head_sha": _SHA, "name": "guard", "status": "completed", "conclusion": "skipped"}],
        _SHA,
        required_contexts=(),
    )
    assert ok is False
    assert "guard" in detail


def test_ci_gate_rejects_duplicate_skipped_and_success() -> None:
    ok, detail = ci_gate_for_commit(
        [
            {"head_sha": _SHA, "name": "tests", "status": "completed", "conclusion": "skipped"},
            {"head_sha": _SHA, "name": "tests", "status": "completed", "conclusion": "success"},
        ],
        _SHA,
        required_contexts=(),
    )
    assert ok is False
    assert "tests" in detail


def test_missing_required_context_fails_closed() -> None:
    ok, detail = ci_gate_for_commit(
        [
            {"head_sha": _SHA, "name": "quality-gate", "status": "completed", "conclusion": "success"},
        ],
        _SHA,
    )
    assert ok is False
    assert "required checks missing" in detail


def test_skip_allowlist_is_documented_empty() -> None:
    assert SKIPPED_CONCLUSION_ALLOWLIST == frozenset()
