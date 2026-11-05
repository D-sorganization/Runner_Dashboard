"""Decision table for operator drain-mode directives (#3915 slice 1)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from coordination.drain_directive_gate import (
    DEFAULT_DRAIN_OPEN_PR_THRESHOLD,
    classify_pr_create_exemption,
    evaluate_repo_drain_gate,
    is_drain_mode_directive,
    pick_drain_directive,
    should_block_pr_create,
)
from priorities.directives import Directive

NOW = datetime(2026, 11, 3, 14, 0, tzinfo=UTC)
FUTURE = "2026-11-05T07:59:00Z"
PAST = "2025-01-01T00:00:00Z"


def _directive(
    text: str = "Drain mode on Gasification_Model until 2026-11-05 07:59Z: no new PRs",
    *,
    repo: str = "Gasification_Model",
    expires: str = FUTURE,
) -> Directive:
    return Directive(
        id="dir-drain",
        text=text,
        set_by="operator",
        repo=repo,
        priority=1,
        expires=expires,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Drain mode on Gasification_Model until tomorrow", True),
        ("drain mode: hold new PRs", True),
        ("Finish OAuth before new features", False),
    ],
)
def test_is_drain_mode_directive(text: str, expected: bool) -> None:
    assert is_drain_mode_directive(text) is expected


@pytest.mark.unit
def test_pick_drain_directive_prefers_matching_repo_and_active() -> None:
    rows = [
        _directive(repo="Runner_Dashboard"),
        _directive(repo="Gasification_Model", expires=FUTURE),
    ]
    picked = pick_drain_directive(rows, "Gasification_Model", now=NOW)
    assert picked is not None and picked.repo == "Gasification_Model"


@pytest.mark.unit
def test_evaluate_repo_drain_gate_drained_when_at_threshold() -> None:
    status = evaluate_repo_drain_gate(
        open_pr_count=20,
        directive=_directive(),
        threshold=DEFAULT_DRAIN_OPEN_PR_THRESHOLD,
        now=NOW,
    )
    assert status == {
        "drained": True,
        "expires": FUTURE,
        "open_pr_count": 20,
        "threshold": DEFAULT_DRAIN_OPEN_PR_THRESHOLD,
    }


@pytest.mark.unit
def test_evaluate_repo_drain_gate_lifts_below_threshold() -> None:
    status = evaluate_repo_drain_gate(
        open_pr_count=19,
        directive=_directive(),
        threshold=DEFAULT_DRAIN_OPEN_PR_THRESHOLD,
        now=NOW,
    )
    assert status["drained"] is False
    assert status["open_pr_count"] == 19


@pytest.mark.unit
def test_evaluate_repo_drain_gate_lifts_when_directive_expired() -> None:
    status = evaluate_repo_drain_gate(
        open_pr_count=44,
        directive=_directive(expires=PAST),
        threshold=DEFAULT_DRAIN_OPEN_PR_THRESHOLD,
        now=NOW,
    )
    assert status["drained"] is False
    assert status["expires"] == PAST


@pytest.mark.unit
def test_evaluate_repo_drain_gate_not_drained_without_directive() -> None:
    status = evaluate_repo_drain_gate(
        open_pr_count=44,
        directive=None,
        threshold=DEFAULT_DRAIN_OPEN_PR_THRESHOLD,
        now=NOW,
    )
    assert status["drained"] is False
    assert status["expires"] is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("title", "labels", "exempt"),
    [
        ("ci: unblock merge queue", [], True),
        ("feat: new lane", ["ci"], True),
        ("fix(security): rotate token", [], True),
        ("chore: docs", ["red-main"], True),
        ("feat: agent lane", [], False),
        ("fix(runner): unblock CI", [], True),
    ],
)
def test_classify_pr_create_exemption(title: str, labels: list[str], exempt: bool) -> None:
    assert classify_pr_create_exemption(title=title, labels=labels) is exempt


@pytest.mark.unit
@pytest.mark.parametrize(
    ("drained", "exempt", "blocked"),
    [
        (True, False, True),
        (True, True, False),
        (False, False, False),
        (False, True, False),
    ],
)
def test_should_block_pr_create(drained: bool, exempt: bool, blocked: bool) -> None:
    assert should_block_pr_create(drained=drained, exempt=exempt) is blocked
