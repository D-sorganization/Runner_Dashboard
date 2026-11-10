"""Tests for red main/nightly CI Andon alerts (#4299)."""

from __future__ import annotations

import pytest

from backend.andon.intake import AndonIntakeDeps, process_andon_intake
from scripts.ci.workflow_failure_andon import build_workflow_failure_payload, should_alert


def test_should_alert_is_false_for_pull_request_only() -> None:
    assert should_alert("pull_request") is False


@pytest.mark.parametrize("event", ["push", "schedule", "merge_group", "workflow_dispatch"])
def test_should_alert_for_main_path_events(event: str) -> None:
    assert should_alert(event) is True


def test_build_workflow_failure_payload_includes_run_metadata() -> None:
    payload = build_workflow_failure_payload(
        repo="D-sorganization/Runner_Dashboard",
        workflow="CI Standard",
        event_name="push",
        run_id="999",
        head_sha="abc123",
        run_url="https://github.com/D-sorganization/Runner_Dashboard/actions/runs/999",
    )
    assert payload["workflow"] == "CI Standard"
    assert "push" in payload["error"]
    assert "abc123" in payload["error"]


def test_workflow_andon_dedupes_replays() -> None:
    deps = AndonIntakeDeps()
    payload = build_workflow_failure_payload(
        repo="D-sorganization/Runner_Dashboard",
        workflow="CI Nightly (Integration)",
        event_name="schedule",
        run_id="42",
        head_sha="deadbeef",
    )
    first = process_andon_intake(payload, deps=deps)
    second = process_andon_intake(
        build_workflow_failure_payload(
            repo="D-sorganization/Runner_Dashboard",
            workflow="CI Nightly (Integration)",
            event_name="schedule",
            run_id="42",
            head_sha="deadbeef",
        ),
        deps=deps,
    )
    assert first["deduped"] is False
    assert second["deduped"] is True
    assert first["issue_number"] == second["issue_number"]
