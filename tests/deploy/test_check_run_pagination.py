"""A deployment must never qualify a truncated or wrong-commit CI inventory."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy"))
from node_auto_deploy.ci_gate import ci_gate_for_deploy_commit  # noqa: E402
from node_auto_deploy.runner import CycleDeps, _check_runs_for_commit  # noqa: E402
from node_auto_deploy.state import AutoDeployState  # noqa: E402

SHA = "a" * 40
B79_SHA = "b79b270ce6dc520c84dfe4f3267f52999b9ed0e8"  # pragma: allowlist secret


def _run(identifier: int, conclusion: str = "success") -> dict:
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)  # noqa: UP017 - deployment also supports Python 3.10
    return {
        "id": identifier + 1,
        "head_sha": SHA,
        "name": f"check-{identifier}",
        "status": "completed",
        "conclusion": conclusion,
        "started_at": (started + timedelta(seconds=identifier)).isoformat(),
        "check_suite": {"id": 1},
        "app": {"id": 1, "slug": "fixture"},
    }


def _api_run(
    identifier: int,
    *,
    name: str,
    conclusion: str | None,
    status: str,
    suite_id: int,
    started_at: str | None,
    app_id: int = 15368,
) -> dict:
    """Use the actual GitHub CheckRun metadata fields (there is no run_attempt)."""
    return {
        "id": identifier,
        "head_sha": SHA,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "started_at": started_at,
        "completed_at": started_at if status == "completed" else None,
        "check_suite": {"id": suite_id},
        "app": {"id": app_id, "slug": "github-actions"},
    }


def _fetch(get, sha: str = SHA):
    deps = CycleDeps(get, lambda: [], lambda: {}, lambda: 0, AutoDeployState())
    return _check_runs_for_commit(deps, "owner/repo", sha)


def test_failure_on_second_page_blocks_deployment() -> None:
    paths = []

    def get(path):
        paths.append(path)
        runs = [_run(i) for i in range(100)] if "page=2" not in path else [_run(100, "failure")]
        return {"total_count": 101, "check_runs": runs}

    checks = _fetch(get)
    assert len(checks) == 101
    assert len(paths) == 2
    assert not ci_gate_for_deploy_commit(checks, SHA)[0]


@pytest.mark.parametrize(
    "mode",
    ["missing-total", "short-page", "changing-total", "duplicate", "wrong-sha", "missing-sha", "malformed", "too-many"],
)
def test_incomplete_inventory_is_rejected(mode: str) -> None:
    def get(path):
        second = "page=2" in path
        runs = [_run(i) for i in range(100)] if not second else [_run(100)]
        total = 101
        if mode == "missing-total":
            return {"check_runs": runs}
        if mode == "short-page":
            runs = runs[:1]
        if mode == "changing-total" and second:
            total = 102
        if mode == "duplicate" and second:
            runs = [_run(0)]
        if mode == "wrong-sha":
            runs[0]["head_sha"] = "b" * 40
        if mode == "missing-sha":
            runs[0].pop("head_sha")
        if mode == "malformed":
            runs[0] = "not a check"
        if mode == "too-many":
            total = 5001
        return {"total_count": total, "check_runs": runs}

    with pytest.raises(RuntimeError, match="check"):
        _fetch(get)


def test_complete_inventory_does_not_fetch_extra_page() -> None:
    paths = []

    def get(path):
        paths.append(path)
        return {"total_count": 100, "check_runs": [_run(i) for i in range(100)]}

    assert len(_fetch(get)) == 100
    assert len(paths) == 1


def test_real_3c3_same_sha_green_merge_group_survives_cancelled_push_suite() -> None:
    # Redacted metadata from the real 3c3b6cc GitHub check-run response. The
    # exact names, app, suite grouping, conclusions and attempt order are kept.
    checks = [
        _api_run(
            120223503615,
            name="Playwright E2E smoke tests",
            conclusion="cancelled",
            status="completed",
            suite_id=108130291090,
            started_at="2026-10-23T03:27:03Z",
        ),
        _api_run(
            120222853385,
            name="Staff Console e2e (fake providers)",
            conclusion="cancelled",
            status="completed",
            suite_id=108130291090,
            started_at="2026-10-23T03:24:46Z",
        ),
        _api_run(
            120221058229,
            name="Playwright E2E smoke tests",
            conclusion="success",
            status="completed",
            suite_id=108128481767,
            started_at="2026-10-23T03:18:36Z",
        ),
        _api_run(
            120220425879,
            name="Staff Console e2e (fake providers)",
            conclusion="success",
            status="completed",
            suite_id=108128481767,
            started_at="2026-10-23T03:16:23Z",
        ),
    ]
    paths: list[str] = []

    def get(path: str) -> dict:
        paths.append(path)
        return {"total_count": len(checks), "check_runs": checks}

    fetched = _fetch(get)

    assert "filter=all" in paths[0]
    assert ci_gate_for_deploy_commit(fetched, SHA)[0]


def test_latest_same_suite_success_supersedes_earlier_failure() -> None:
    checks = [
        _api_run(
            100,
            name="Docker build",
            conclusion="failure",
            status="completed",
            suite_id=10,
            started_at="2026-10-23T03:00:00Z",
        ),
        _api_run(
            101,
            name="Docker build",
            conclusion="success",
            status="completed",
            suite_id=10,
            started_at="2026-10-23T03:01:00Z",
        ),
    ]

    assert ci_gate_for_deploy_commit(_fetch(lambda _: {"total_count": 2, "check_runs": checks}), SHA)[0]


def test_latest_same_suite_failure_blocks_earlier_success() -> None:
    checks = [
        _api_run(
            100,
            name="Docker build",
            conclusion="success",
            status="completed",
            suite_id=10,
            started_at="2026-10-23T03:00:00Z",
        ),
        _api_run(
            101,
            name="Docker build",
            conclusion="failure",
            status="completed",
            suite_id=10,
            started_at="2026-10-23T03:01:00Z",
        ),
    ]

    assert not ci_gate_for_deploy_commit(_fetch(lambda _: {"total_count": 2, "check_runs": checks}), SHA)[0]


@pytest.mark.parametrize("pending_status", ["queued", "in_progress", "requested", "waiting", "pending"])
def test_latest_pending_same_suite_attempt_blocks_earlier_success(pending_status: str) -> None:
    checks = [
        _api_run(
            100,
            name="Docker build",
            conclusion="success",
            status="completed",
            suite_id=10,
            started_at="2026-10-23T03:00:00Z",
        ),
        _api_run(
            101,
            name="Docker build",
            conclusion=None,
            status=pending_status,
            suite_id=10,
            started_at=None,
        ),
    ]

    assert not ci_gate_for_deploy_commit(_fetch(lambda _: {"total_count": 2, "check_runs": checks}), SHA)[0]


@pytest.mark.parametrize(
    "bad_field",
    [
        "check_suite",
        "app",
        "suite_id",
        "app_id",
        "started_at",
        "run_id_zero",
        "suite_id_bool",
        "app_id_zero",
        "started_at_naive",
        "started_at_invalid",
        "foreign_head_sha",
        "missing_conclusion",
        "unknown_conclusion",
    ],
)
def test_missing_or_malformed_attempt_metadata_fails_closed(bad_field: str) -> None:
    check = _api_run(
        100,
        name="Docker build",
        conclusion="success",
        status="completed",
        suite_id=10,
        started_at="2026-10-23T03:00:00Z",
    )
    if bad_field in {"check_suite", "app"}:
        check.pop(bad_field)
    elif bad_field in {"suite_id", "app_id"}:
        check["check_suite" if bad_field == "suite_id" else "app"].pop("id")
    elif bad_field == "started_at":
        check.pop("started_at")
    elif bad_field == "run_id_zero":
        check["id"] = 0
    elif bad_field == "suite_id_bool":
        check["check_suite"]["id"] = True
    elif bad_field == "app_id_zero":
        check["app"]["id"] = 0
    elif bad_field == "started_at_naive":
        check["started_at"] = "2026-10-23T03:00:00"
    elif bad_field == "started_at_invalid":
        check["started_at"] = "not-an-instant"
    elif bad_field == "foreign_head_sha":
        check["head_sha"] = "b" * 40
    elif bad_field == "missing_conclusion":
        check.pop("conclusion")
    else:
        check["conclusion"] = "unrecognized"

    with pytest.raises(RuntimeError, match="check"):
        _fetch(lambda _: {"total_count": 1, "check_runs": [check]})


@pytest.mark.parametrize("reverse", [False, True])
def test_parallel_same_suite_checks_allow_nonmonotonic_start_times(reverse: bool) -> None:
    # Redacted exact record metadata from b79b270: GitHub assigned the newer
    # skipped CheckRun an earlier start (and completion) time than the success.
    earlier_id = _api_run(
        120258276607,
        name="tests",
        conclusion="success",
        status="completed",
        suite_id=108160610760,
        started_at="2026-10-23T05:30:06Z",
    )
    earlier_id.update(
        head_sha=B79_SHA,
        completed_at="2026-10-23T05:30:09Z",
        app={"id": 15368, "slug": "github-actions"},
    )
    newer_id = _api_run(
        120258277376,
        name="tests",
        conclusion="skipped",
        status="completed",
        suite_id=108160610760,
        started_at="2026-10-23T05:30:04Z",
    )
    newer_id.update(
        head_sha=B79_SHA,
        completed_at="2026-10-23T05:30:03Z",
        app={"id": 15368, "slug": "github-actions"},
    )
    checks = [earlier_id, newer_id]
    if reverse:
        checks.reverse()

    fetched = _fetch(lambda _: {"total_count": 2, "check_runs": checks}, B79_SHA)

    assert fetched == [newer_id]


def test_raw_check_cap_is_enforced_before_attempt_normalization() -> None:
    page1 = [
        _api_run(
            1000 + i,
            name="One repeated check",
            conclusion="cancelled",
            status="completed",
            suite_id=10,
            started_at="2026-10-23T03:00:00Z",
        )
        for i in range(100)
    ]

    with pytest.raises(RuntimeError, match="check"):
        _fetch(lambda _: {"total_count": 5001, "check_runs": page1})
