"""Drain-mode status derived from operator directives (#3915 slice 1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from drain_mode import DEFAULT_THRESHOLD, drain_status, is_drain_directive, threshold_from_env
from priorities.directives import Directive

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def _directive(text: str, repo: str = "Gasification_Model", expires: str = "") -> Directive:
    return Directive(id="dir-test", text=text, set_by="op", repo=repo, expires=expires)


GM_DRAIN = _directive("Drain mode on Gasification_Model: no new PRs except CI/runner, security, red-main fixes")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Drain mode on Gasification_Model until Thursday", True),
        ("drain-mode: GM", True),
        ("DRAIN MODE", True),
        ("Finish the coordination API before new roles", False),
        ("Do not drain the queue", False),
    ],
)
def test_is_drain_directive(text: str, expected: bool) -> None:
    assert is_drain_directive(_directive(text)) is expected


EXPIRED = _directive("Drain mode on GM", expires=(NOW - timedelta(minutes=1)).isoformat())
FLEETWIDE = _directive("Drain mode everywhere", repo="*")


@pytest.mark.parametrize(
    ("repo", "directives", "count", "threshold", "drained", "reason"),
    [
        ("Gasification_Model", [], 44, 20, False, "no_directive"),
        ("Gasification_Model", [GM_DRAIN], 19, 20, False, "below_threshold"),
        ("Gasification_Model", [EXPIRED], 44, 20, False, "no_directive"),
        ("Gasification_Model", [GM_DRAIN], None, 20, True, "count_unavailable"),
        ("Gasification_Model", [GM_DRAIN], 8, 5, True, "active"),
        ("Runner_Dashboard", [GM_DRAIN, FLEETWIDE], 50, 20, False, "no_directive"),
    ],
)
def test_drain_status_table(
    repo: str, directives: list[Directive], count: int | None, threshold: int, drained: bool, reason: str
) -> None:
    status = drain_status(repo, directives, open_pr_count=count, now=NOW, threshold=threshold)
    assert (status["drained"], status["reason"], status["threshold"]) == (drained, reason, threshold)
    assert status["open_pr_count"] == count


def test_drained_at_threshold_reports_the_directive() -> None:
    expires = "2026-11-05T07:59:00Z"
    status = drain_status("Gasification_Model", [_directive("Drain mode", expires=expires)], open_pr_count=20, now=NOW)
    assert status == {
        "repo": "Gasification_Model",
        "drained": True,
        "reason": "active",
        "directive_id": "dir-test",
        "expires": expires,
        "open_pr_count": 20,
        "threshold": DEFAULT_THRESHOLD,
    }


@pytest.mark.parametrize(("raw", "expected"), [(None, 20), ("", 20), ("35", 35), ("0", 20), ("abc", 20)])
def test_threshold_from_env(monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: int) -> None:
    if raw is None:
        monkeypatch.delenv("DRAIN_OPEN_PR_THRESHOLD", raising=False)
    else:
        monkeypatch.setenv("DRAIN_OPEN_PR_THRESHOLD", raw)
    assert threshold_from_env() == expected


def test_drain_status_rejects_bad_repo() -> None:
    with pytest.raises(ValueError, match="repo"):
        drain_status("../etc", [], open_pr_count=0, now=NOW)
