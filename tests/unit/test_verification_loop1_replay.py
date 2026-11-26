"""Verification Loop 1 (#4683): offline replay of the RD#4682 merge_group failure.

Red-first tests for ``scripts.verification.offline_replay``. Fixture inputs
(``tests/fixtures/verification/rd4682/inputs.json``) are kept apart from the
expected outcomes (``expected.json``) so a replay can never read its answer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.verification import offline_replay as replay

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "verification" / "rd4682"
WORKFLOW = ROOT / ".github" / "workflows" / "ci-standard.yml"
DOC = ROOT / "docs" / "verification" / "loop1-rd4682.md"

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def inputs() -> dict:
    return json.loads((FIXTURE_DIR / "inputs.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def expected() -> dict:
    return json.loads((FIXTURE_DIR / "expected.json").read_text(encoding="utf-8"))


def test_inputs_never_contain_expected_outcomes(inputs: dict) -> None:
    blob = json.dumps(inputs)
    for forbidden in ("expected", '"outcome"', '"conclusion"'):
        assert forbidden not in blob


def test_rd4682_replay_fails_repeatably(inputs: dict) -> None:
    runs = [replay.replay_case(inputs["cases"]["rd4682"]) for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]
    assert runs[0]["outcome"] == "fail"
    assert runs[0]["failure_class"] is None


def test_calibration_replay_passes_repeatably(inputs: dict) -> None:
    runs = [replay.replay_case(inputs["cases"]["calibration"]) for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]
    assert runs[0]["outcome"] == "pass"
    assert runs[0]["failure_class"] == "stalled"


def test_replay_matches_expected_file(inputs: dict, expected: dict) -> None:
    for name, case in inputs["cases"].items():
        got = replay.replay_case(case)
        assert got["outcome"] == expected["cases"][name]["outcome"]
        assert got["failure_class"] == expected["cases"][name]["failure_class"]


def test_replay_uses_virtual_time_and_no_real_processes(inputs: dict) -> None:
    result = replay.replay_case(inputs["cases"]["calibration"])
    assert result["virtual_elapsed_s"] > 0.3  # orphan grace was waited out virtually
    assert result["terminated_pids"] == [102]


def test_combined_tree_equals_green_pr_head_tree(inputs: dict) -> None:
    for case in inputs["cases"].values():
        pre = case["pre_check"]
        assert pre["merge_group_tree"] == pre["pr_head_tree"]


def test_stop_gate_blocks_shadow_mode_when_pre_check_cannot_discriminate(inputs: dict, expected: dict) -> None:
    verdict = replay.evaluate_stop_gate(inputs["cases"]["rd4682"], inputs["cases"]["calibration"])
    assert verdict["decision"] == expected["stop_gate"]["decision"] == "STOP_BEFORE_SHADOW"
    assert verdict["pre_check_discriminates"] is False
    assert verdict["schedule_available_pre_check"] is False


def test_event_classification_matches_observed_merge_group_run(inputs: dict) -> None:
    table = replay.classify_job_events(WORKFLOW)
    assert "merge_group" in replay.workflow_triggers(WORKFLOW)
    observed_skipped = {name for name, concl in inputs["observed_merge_group_jobs"].items() if concl == "skipped"}
    predicted_skipped = {j for j, kind in table.items() if kind in {"pull_request_only", "not_merge_group"}}
    assert observed_skipped <= predicted_skipped
    assert table["pr-guard"] == "pull_request_only"
    assert table["security-scan"] == "not_merge_group"
    assert table["lint"] == "both"


def test_doc_lists_every_ci_standard_job_and_the_failed_step() -> None:
    text = DOC.read_text(encoding="utf-8")
    for job in replay.classify_job_events(WORKFLOW):
        assert f"`{job}`" in text
    assert "Run Python tests" in text
    assert "test_watchdog_waits_for_orphan_then_expires" in text
