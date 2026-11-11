"""Characterization tests for RM#2883: record lenient CI gate behavior before fail-closed fix.

These assertions document today's production semantics (skipped satisfies, duplicate
names aggregate). After the gate change they are updated to expect fail-closed behavior.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

from node_auto_deploy.check_runs import check_name_passes  # noqa: E402
from node_auto_deploy.ci_gate import ci_gate_for_commit  # noqa: E402

_SHA = "a" * 40


def test_characterization_skipped_check_name_passed_alone() -> None:
    assert check_name_passes(
        [{"status": "completed", "conclusion": "skipped", "name": "guard"}],
    )


def test_characterization_tier_gated_duplicate_skipped_plus_success_passed() -> None:
    assert check_name_passes(
        [
            {"status": "completed", "conclusion": "skipped", "name": "lint"},
            {"status": "completed", "conclusion": "success", "name": "lint"},
        ],
    )


def test_characterization_ci_gate_accepted_skipped_only_payload() -> None:
    ok, _ = ci_gate_for_commit(
        [{"head_sha": _SHA, "name": "guard", "status": "completed", "conclusion": "skipped"}],
        _SHA,
    )
    assert ok is True


def test_characterization_ci_gate_accepted_duplicate_skipped_and_success() -> None:
    ok, _ = ci_gate_for_commit(
        [
            {"head_sha": _SHA, "name": "tests", "status": "completed", "conclusion": "skipped"},
            {"head_sha": _SHA, "name": "tests", "status": "completed", "conclusion": "success"},
        ],
        _SHA,
    )
    assert ok is True
