"""Release/deploy gate: the latest run per check name decides (#4419, #4425).

The shapes come from the check runs of main commit 74b0a1a0, whose release was
skipped although CI was green: the merge queue ran CI under ``merge_group`` and
GitHub ran it again under ``push``, so 33 of 42 check names had more than one
run, and several jobs skipped by design.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
_FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(REPO / "deploy"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from main_auto_release.gate import commit_passes_publisher_ci_gate  # noqa: E402
from node_auto_deploy.check_runs import check_name_passes, latest_verdict_runs  # noqa: E402
from node_auto_deploy.ci_gate import ci_gate_for_commit, ci_gate_for_deploy_commit  # noqa: E402
from required_context_helper import (  # noqa: E402
    policy_required_context_names,
    success_runs_for_required_contexts,
)

_SHA = "7" * 40
_MERGE_GROUP_SUITE = 9001
_PUSH_SUITE = 9002


def _run(name: str, conclusion: str | None, run_id: int, *, suite: int = _PUSH_SUITE) -> dict[str, Any]:
    return {
        "id": run_id,
        "head_sha": _SHA,
        "name": name,
        "status": "completed" if conclusion else "in_progress",
        "conclusion": conclusion,
        "app": {"id": 1},
        "check_suite": {"id": suite},
        "started_at": f"2026-11-13T18:{run_id % 60:02d}:00Z",
    }


def _by_conclusions(*conclusions: str | None) -> list[dict[str, Any]]:
    """Runs of one name in creation order; odd positions are the merge-queue suite."""
    return [
        _run("job", conclusion, 100 + index, suite=_MERGE_GROUP_SUITE if index % 2 == 0 else _PUSH_SUITE)
        for index, conclusion in enumerate(conclusions)
    ]


@pytest.mark.parametrize(
    "conclusions",
    [
        ("success", "success"),  # merge_group then push: 16 names on 74b0a1a0
        ("skipped", "success"),
        ("success", "skipped"),
        ("success", "skipped", "success"),
        ("success", "success", "skipped"),
        ("cancelled", "success", "success"),
        ("failure", "success"),  # a failed attempt that was re-run and passed
        ("failure", "cancelled", "success"),
        ("success", "neutral"),
    ],
)
def test_name_passes_when_the_latest_verdict_is_success(conclusions: tuple[str, ...]) -> None:
    assert check_name_passes(_by_conclusions(*conclusions)) is True


@pytest.mark.parametrize(
    "conclusions",
    [
        ("success", "failure"),  # the newest attempt failed: fail closed
        ("success", "success", "failure"),
        ("success", "timed_out"),
        ("success", "action_required"),
        ("failure", "skipped"),  # a later skip does not repair a failure
        ("failure", "cancelled"),
        ("failure",),
        ("cancelled",),
        ("cancelled", "cancelled"),
        ("cancelled", "skipped"),
        ("stale",),
    ],
)
def test_name_fails_when_the_latest_verdict_is_not_success(conclusions: tuple[str, ...]) -> None:
    assert check_name_passes(_by_conclusions(*conclusions)) is False
    assert check_name_passes(_by_conclusions(*conclusions), required=False) is False


@pytest.mark.parametrize("conclusions", [("skipped",), ("skipped", "skipped", "skipped"), ("neutral",)])
def test_skip_only_name_passes_unless_it_is_a_required_context(conclusions: tuple[str, ...]) -> None:
    runs = _by_conclusions(*conclusions)
    assert check_name_passes(runs, required=False) is True
    assert check_name_passes(runs, required=True) is False
    assert check_name_passes(runs) is False, "the default stays strict"


def test_order_is_by_run_id_not_by_list_position() -> None:
    newest_failed = [_run("job", "failure", 300), _run("job", "success", 200)]
    newest_passed = [_run("job", "success", 300), _run("job", "failure", 200)]
    assert check_name_passes(newest_failed) is False
    assert check_name_passes(newest_passed) is True
    assert [run["id"] for run in latest_verdict_runs(newest_failed)] == [300]


def test_runs_that_cannot_be_ordered_must_all_pass() -> None:
    unordered = [
        {"name": "job", "status": "completed", "conclusion": "success"},
        {"name": "job", "status": "completed", "conclusion": "failure"},
    ]
    assert check_name_passes(unordered) is False
    assert check_name_passes(list(reversed(unordered))) is False
    assert check_name_passes([dict(unordered[0]), dict(unordered[0])]) is True


def test_no_completed_run_never_passes() -> None:
    assert check_name_passes([]) is False
    assert check_name_passes(_by_conclusions(None), required=False) is False


def _merge_queue_commit() -> list[dict[str, Any]]:
    """Every required context ran green twice; other jobs skipped or were superseded."""
    runs: list[dict[str, Any]] = []
    next_id = 1000
    for name in policy_required_context_names():
        if name == "guard":  # pull-request guard: its only run on a main commit is skipped
            runs.append(_run(name, "skipped", next_id, suite=_MERGE_GROUP_SUITE))
            next_id += 2
            continue
        runs.append(_run(name, "success", next_id, suite=_MERGE_GROUP_SUITE))
        runs.append(_run(name, "success", next_id + 1, suite=_PUSH_SUITE))
        next_id += 2
    runs.append(_run("Auto-deploy live regression", "skipped", next_id))
    runs.append(_run("Deploy docs preview", "skipped", next_id + 1, suite=_MERGE_GROUP_SUITE))
    runs.append(_run("Deploy docs preview", "skipped", next_id + 2))
    runs.append(_run("Build and scan image", "cancelled", next_id + 3, suite=_MERGE_GROUP_SUITE))
    runs.append(_run("Build and scan image", "success", next_id + 4))
    runs.append(_run("Vitest + Coverage", "skipped", next_id + 5, suite=_MERGE_GROUP_SUITE))
    runs.append(_run("Vitest + Coverage", "success", next_id + 6))
    return runs


def test_merge_queue_commit_with_duplicate_and_skipped_checks_passes_every_gate() -> None:
    runs = _merge_queue_commit()
    for gate in (ci_gate_for_commit, ci_gate_for_deploy_commit, commit_passes_publisher_ci_gate):
        ok, detail = gate(runs, _SHA)
        assert ok is True, f"{gate.__name__}: {detail}"


def _recorded_74b0a1a_runs() -> list[dict[str, Any]]:
    payload = json.loads((_FIXTURES / "74b0a1a_check_runs.json").read_text(encoding="utf-8"))
    assert "74b0a1a" in payload["fetched_from"]
    return [
        {
            "id": run["id"],
            "head_sha": _SHA,
            "name": run["name"],
            "status": "completed",
            "conclusion": run["conclusion"],
            "app": {"id": 1},
            "check_suite": {"id": run["suite"]},
        }
        for run in payload["check_runs"]
    ]


def test_recorded_74b0a1a_check_runs_pass_the_deploy_and_release_gates() -> None:
    """The commit whose release and deploy were skipped (#4419, #4425)."""
    runs = _recorded_74b0a1a_runs()
    by_name: dict[str, list[str]] = {}
    for run in sorted(runs, key=lambda item: item["id"]):
        by_name.setdefault(run["name"], []).append(run["conclusion"])
    assert len(runs) == 123
    assert by_name["quality-gate"] == ["success", "success"]
    assert by_name["tests"] == ["success", "success", "skipped"]
    assert by_name["guard"] == ["skipped"]
    assert by_name["Auto-deploy live regression"] == ["skipped"]
    assert by_name["Backend smoke test without a Repository_Management checkout"] == ["success", "success"]

    for gate in (ci_gate_for_deploy_commit, commit_passes_publisher_ci_gate):
        ok, detail = gate(runs, _SHA)
        assert ok is True, f"{gate.__name__}: {detail}"


def test_recorded_74b0a1a_commit_blocks_when_a_required_push_run_fails() -> None:
    runs = _recorded_74b0a1a_runs()
    newest = max((run for run in runs if run["name"] == "quality-gate"), key=lambda item: item["id"])
    newest["conclusion"] = "failure"

    ok, detail = ci_gate_for_deploy_commit(runs, _SHA)
    assert ok is False
    assert "quality-gate" in detail


def test_failure_in_the_push_run_after_a_green_merge_queue_run_blocks() -> None:
    runs = _merge_queue_commit()
    name = policy_required_context_names()[0]
    newest = max(run["id"] for run in runs if run["name"] == name)
    for run in runs:
        if run["id"] == newest:
            run["conclusion"] = "failure"

    for gate in (ci_gate_for_commit, ci_gate_for_deploy_commit, commit_passes_publisher_ci_gate):
        ok, detail = gate(runs, _SHA)
        assert ok is False, gate.__name__
        assert name in detail


def test_optional_check_whose_latest_run_failed_blocks() -> None:
    runs = success_runs_for_required_contexts(_SHA)
    runs += [_run("Vitest + Coverage", "success", 5000), _run("Vitest + Coverage", "failure", 5001)]
    ok, detail = ci_gate_for_commit(runs, _SHA)
    assert ok is False
    assert "Vitest + Coverage" in detail


def test_required_context_that_only_skipped_blocks() -> None:
    name = policy_required_context_names()[0]
    runs = [run for run in success_runs_for_required_contexts(_SHA) if run["name"] != name]
    runs += [_run(name, "skipped", 6000, suite=_MERGE_GROUP_SUITE), _run(name, "skipped", 6001)]
    ok, detail = ci_gate_for_commit(runs, _SHA)
    assert ok is False
    assert name in detail


def test_pending_duplicate_still_makes_the_gate_wait() -> None:
    runs = _merge_queue_commit()
    runs.append(_run(policy_required_context_names()[0], None, 7000))
    ok, detail = ci_gate_for_commit(runs, _SHA)
    assert ok is False
    assert "still running" in detail
