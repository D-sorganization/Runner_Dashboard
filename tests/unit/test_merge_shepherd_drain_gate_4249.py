"""#4249 B-04: the merge shepherd's drain gate, behind a flag that is off by default."""

from __future__ import annotations

from typing import Any

import pytest
from coordination import merge_shepherd_pass as pass_mod
from coordination.merge_shepherd_pass import (
    DRAIN_GATE_ENV,
    PassConfig,
    drain_gate_enabled,
    node_to_candidate,
    plan_repo_pass,
    run_merge_shepherd_pass,
)

from tests.unit.test_merge_shepherd_pass import _open_pr_node, _review_pass

REPO = "D-sorganization/Runner_Dashboard"


def _plan(nodes: list[dict[str, Any]], *, drained: bool) -> tuple[list[Any], list[dict[str, Any]]]:
    actions, skips, backoff = plan_repo_pass(
        REPO,
        nodes,
        skip_list=frozenset(),
        holds=(),
        armed_count=0,
        queue_depth=0,
        live=False,
        review_passes=[_review_pass(int(n["number"]), str(n["headRefOid"])) for n in nodes],
        drained=drained,
    )
    assert backoff is False
    return actions, skips


def _exempt_node(number: int) -> dict[str, Any]:
    node = _open_pr_node(number)
    node["title"] = "fix(ci): unblock the runner pool"
    return node


@pytest.mark.parametrize("raw", ["", "0", "false", "no", "off", "maybe"])
def test_the_gate_is_off_unless_the_flag_is_set(raw: str) -> None:
    assert drain_gate_enabled({DRAIN_GATE_ENV: raw}) is False
    assert drain_gate_enabled({}) is False


@pytest.mark.parametrize("raw", ["1", "true", "YES", " on "])
def test_the_flag_turns_the_gate_on(raw: str) -> None:
    assert drain_gate_enabled({DRAIN_GATE_ENV: raw}) is True


def test_a_drained_repository_arms_only_exempt_prs() -> None:
    actions, skips = _plan([_open_pr_node(200), _exempt_node(201)], drained=True)
    assert [action.pr_number for action in actions] == [201]
    assert {"repo": REPO, "pr": 200, "reason": "drained"} in skips


def test_a_repository_that_is_not_drained_arms_as_before() -> None:
    actions, skips = _plan([_open_pr_node(200), _exempt_node(201)], drained=False)
    assert sorted(action.pr_number for action in actions) == [200, 201]
    assert skips == []


def test_the_candidate_carries_the_exemption() -> None:
    assert node_to_candidate(REPO, _exempt_node(1), is_drained=True).is_exempt is True
    assert node_to_candidate(REPO, _open_pr_node(2), is_drained=True).is_exempt is False


def _run(monkeypatch: pytest.MonkeyPatch, *, flag: str, drained: Any) -> tuple[Any, list[tuple[str, int]]]:
    """One pass over a single repository whose open PR is not exempt."""
    asked: list[tuple[str, int]] = []

    def is_drained(repo: str, open_prs: int) -> bool:
        asked.append((repo, open_prs))
        if isinstance(drained, Exception):
            raise drained
        return bool(drained)

    def fetch(_owner: str, _name: str) -> dict[str, Any]:
        return {"repository": {"pullRequests": {"nodes": [_open_pr_node(300)]}}}

    monkeypatch.setenv(DRAIN_GATE_ENV, flag)
    monkeypatch.setattr(pass_mod, "SHEPHERD_REPOS", (REPO,))
    monkeypatch.setattr(pass_mod, "collect_review_passes_from_store", lambda **_: [_review_pass(300, "sha300")])
    monkeypatch.setattr(pass_mod, "load_skip_list", lambda _path: frozenset())
    summary = run_merge_shepherd_pass(config=PassConfig(live=False, is_drained=is_drained), fetch_graphql=fetch)
    return summary, asked


def test_with_the_flag_off_the_pass_never_asks_about_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    summary, asked = _run(monkeypatch, flag="", drained=True)
    assert asked == []
    assert summary.repos[0]["planned_arms"] == 1


def test_with_the_flag_on_a_drained_repository_is_not_armed(monkeypatch: pytest.MonkeyPatch) -> None:
    summary, asked = _run(monkeypatch, flag="1", drained=True)
    assert asked == [(REPO, 1)]
    assert summary.repos[0]["planned_arms"] == 0
    assert {"repo": REPO, "pr": 300, "reason": "drained"} in summary.skipped


def test_with_the_flag_on_a_repository_that_is_not_drained_is_armed(monkeypatch: pytest.MonkeyPatch) -> None:
    summary, _ = _run(monkeypatch, flag="1", drained=False)
    assert summary.repos[0]["planned_arms"] == 1


def test_an_unreadable_drain_state_is_reported_and_does_not_stop_arming(monkeypatch: pytest.MonkeyPatch) -> None:
    summary, _ = _run(monkeypatch, flag="1", drained=OSError("directives unreadable"))
    assert summary.repos[0]["planned_arms"] == 1
    assert any("drain state unreadable" in error for error in summary.errors)
