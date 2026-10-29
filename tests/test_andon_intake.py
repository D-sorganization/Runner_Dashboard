"""Tests for A3 hub andon intake route and fingerprint deduplication (#2902)."""

from __future__ import annotations

from typing import Any

import pytest
from backend.andon.intake import (
    AndonIntakeDeps,
    AndonIntakeError,
    andon_router,
    build_andon_app,
    process_andon_intake,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shared_scripts.andon_fingerprint import AndonSchemaError


def _sample_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "repo": "D-sorganization/Runner_Dashboard",
        "workflow": "CI Standard",
        "check": "tests (3.11, 1, 1)",
        "file": "tests/test_example.py",
        "error": (
            "AssertionError: expected 200 got 500 at 2026-10-29T12:00:00Z on runner-fleet-1 commit deadbeef1 line 42"
        ),
    }
    base.update(overrides)
    return base


def test_process_andon_intake_rejects_invalid_schema() -> None:
    """Invalid A1 payloads must be rejected by DbC precondition checks."""
    deps = AndonIntakeDeps()
    with pytest.raises(AndonSchemaError):
        process_andon_intake({"repo": "D-sorganization/Runner_Dashboard", "workflow": "CI"}, deps=deps)

    with pytest.raises(AndonSchemaError):
        process_andon_intake(_sample_payload(error="   "), deps=deps)

    client = TestClient(build_andon_app(deps))
    resp = client.post("/api/v1/andon", json={"repo": "only-repo"})
    assert resp.status_code == 422
    assert "::error::" in str(resp.json().get("detail", ""))


def test_post_andon_dedupes_by_fingerprint() -> None:
    """Volatile error text with the same root cause must dedupe to the existing open andon issue."""
    deps = AndonIntakeDeps()
    client = TestClient(build_andon_app(deps))

    first = client.post(
        "/api/v1/andon",
        json=_sample_payload(
            error="AssertionError: boom at 2026-10-29T10:00:00Z commit abcdef123456 on runner-slot-1 line 10:2"
        ),
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["deduped"] is False
    assert isinstance(first_body["issue_number"], int)

    second = client.post(
        "/api/v1/andon",
        json=_sample_payload(
            error="AssertionError: boom at 2026-10-29T11:30:00Z commit 9876543210ab on runner-slot-9 line 99:8"
        ),
    )
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["deduped"] is True
    assert second_body["issue_number"] == first_body["issue_number"]
    assert second_body["fingerprint"] == first_body["fingerprint"]
    assert len(deps.created_issues) == 1
    assert len(deps.issue_comments[first_body["issue_number"]]) == 1


def test_replay_ten_times_one_issue() -> None:
    """Replaying the identical andon payload 10 times produces 1 issue and at most 1 comment."""
    deps = AndonIntakeDeps()
    client = TestClient(build_andon_app(deps))
    payload = _sample_payload()

    results = [client.post("/api/v1/andon", json=payload) for _ in range(10)]
    assert all(r.status_code == 200 for r in results)

    bodies = [r.json() for r in results]
    issue_numbers = {b["issue_number"] for b in bodies}
    assert len(issue_numbers) == 1
    issue_no = bodies[0]["issue_number"]
    assert bodies[0]["deduped"] is False
    assert all(b["deduped"] is True for b in bodies[1:])
    assert len(deps.created_issues) == 1
    assert len(deps.issue_comments[issue_no]) <= 1


def test_bounded_retry_then_surfaced_error_on_github_failure() -> None:
    """GitHub client failures retry a bounded number of times then surface 502 with ::error::."""
    attempts = 0

    def failing_list(_repo: str) -> list[dict[str, Any]]:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("upstream GitHub 503")

    deps = AndonIntakeDeps(list_open_andons=failing_list, max_retries=3)
    with pytest.raises(AndonIntakeError, match="::error::"):
        process_andon_intake(_sample_payload(), deps=deps)
    assert attempts == 3

    attempts = 0
    client = TestClient(build_andon_app(deps))
    resp = client.post("/api/v1/andon", json=_sample_payload())
    assert resp.status_code == 502
    assert "::error::" in str(resp.json().get("detail", ""))
    assert attempts == 3


def test_non_loopback_without_scope_is_rejected() -> None:
    """Requests lacking local loopback ingress and fleet.control scope are denied."""
    app = FastAPI()
    app.include_router(andon_router)
    client = TestClient(app, client=("203.0.113.10", 54321))
    resp = client.post("/api/v1/andon", json=_sample_payload())
    assert resp.status_code == 403
