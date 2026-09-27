"""WP-2.5 (#1605 part 2): ``executing -> done`` needs verified children and checked acceptance criteria.

- Every child is merged, and its PR is confirmed merged with green head CI by a PR-number probe.
- Every acceptance criterion's latest recorded check passed (a scripted result or a ``qa-verifier`` run).
- ``POST .../executor/complete`` lists the unmet conditions (409) or enters ``done`` through the
  ``acceptance`` gate. A lookup failure is unmet, never a pass (fail-closed).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from code_requests.acceptance import record_check, unmet_conditions  # noqa: E402
from code_requests.executor_models import (  # noqa: E402
    AcceptanceCheck,
    ChildExecutionRecord,
    ChildExecutionState,
)
from code_requests.executor_stage import ExecutorPipeline  # noqa: E402
from code_requests.executor_store import PipelineStore  # noqa: E402
from code_requests.lifecycle import TransitionGate  # noqa: E402
from code_requests.model import CodeRequest, CodeRequestState, Requester, RequesterKind  # noqa: E402
from code_requests.store import CodeRequestStore  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from identity import Principal, require_principal  # noqa: E402
from routers import code_requests_executor as executor_router  # noqa: E402
from server import app  # noqa: E402
from staff.verification import GitHubLookupError, PullRequest  # noqa: E402

CR_ID = "cr-accept-1"


class FakeProbe:
    """PR-number probe returning canned PRs; a missing number raises like a failed lookup."""

    def __init__(self, prs: dict[int, PullRequest]) -> None:
        self.prs = prs

    def get(self, repo: str, number: int) -> PullRequest:
        if number not in self.prs:
            raise GitHubLookupError(f"no PR #{number}")
        return self.prs[number]


def _child(
    key: str, pr: int | None = 11, *, state: ChildExecutionState = ChildExecutionState.MERGED
) -> ChildExecutionRecord:
    return ChildExecutionRecord(
        key=key,
        repository="Runner_Dashboard",
        title=f"Child {key}",
        state=state,
        pr_number=pr,
        acceptance_criteria=[f"{key} works", f"{key} is tested"],
    )


def _check(criterion: str, *, passed: bool = True, source: str = "script") -> AcceptanceCheck:
    return AcceptanceCheck(
        criterion=criterion, passed=passed, evidence="pytest: 3 passed", source=source, recorded_by="op"
    )


def _checked(child: ChildExecutionRecord) -> ChildExecutionRecord:
    for criterion in child.acceptance_criteria:
        record_check(child, _check(criterion))
    return child


GREEN = FakeProbe({11: PullRequest(11, "merged", "green"), 12: PullRequest(12, "merged", "green")})


# ── the pure check ───────────────────────────────────────────────────────────
@pytest.mark.unit
def test_all_children_merged_green_and_checked_have_no_unmet_conditions() -> None:
    children = {"a": _checked(_child("a", 11)), "b": _checked(_child("b", 12))}
    assert unmet_conditions(children, GREEN) == []


@pytest.mark.unit
def test_an_empty_pipeline_is_not_acceptable() -> None:
    assert unmet_conditions({}, GREEN) == ["the pipeline has no children"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("child", "probe", "fragment"),
    [
        (_child("a", state=ChildExecutionState.CI), GREEN, "is ci, not merged"),
        (_child("a", pr=None), GREEN, "has no pull request"),
        (_child("a"), FakeProbe({11: PullRequest(11, "closed", "green")}), "closed without merging"),
        (_child("a"), FakeProbe({11: PullRequest(11, "open", "green")}), "PR #11 is open, not merged"),
        (_child("a"), FakeProbe({11: PullRequest(11, "merged", "red")}), "head CI is failing"),
        (_child("a"), FakeProbe({11: PullRequest(11, "merged", "pending")}), "still pending"),
        (_child("a"), FakeProbe({}), "could not be verified"),
    ],
)
def test_each_unverified_child_is_named(child: ChildExecutionRecord, probe: FakeProbe, fragment: str) -> None:
    unmet = unmet_conditions({"a": _checked(child)}, probe)
    assert len(unmet) == 1 and unmet[0].startswith("child 'a'") and fragment in unmet[0], unmet


@pytest.mark.unit
def test_every_criterion_needs_a_passing_latest_check() -> None:
    child = _child("a")
    record_check(child, _check("a works", passed=True))
    record_check(child, _check("a is tested", passed=True))
    record_check(child, _check("a is tested", passed=False, source="qa-verifier"))
    assert unmet_conditions({"a": child}, GREEN) == ["child 'a': acceptance check failed for 'a is tested'"]

    unchecked = _child("b", 12)
    assert unmet_conditions({"b": unchecked}, GREEN) == [
        "child 'b': no recorded acceptance check for 'b works'",
        "child 'b': no recorded acceptance check for 'b is tested'",
    ]


@pytest.mark.unit
def test_a_check_must_name_one_of_the_childs_criteria() -> None:
    with pytest.raises(ValueError, match="not an acceptance criterion"):
        record_check(_child("a"), _check("something else"))


# ── the routes ───────────────────────────────────────────────────────────────
def _request(state: CodeRequestState = CodeRequestState.EXECUTING) -> CodeRequest:
    return CodeRequest(
        id=CR_ID,
        repository="Runner_Dashboard",
        title="Widget pipeline",
        issue_number=100,
        state=state,
        requester=Requester(id="tester", kind=RequesterKind.HUMAN),
        created_at="2026-09-27T12:00:00Z",
        updated_at="2026-09-27T12:00:00Z",
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    requests = MagicMock(spec=CodeRequestStore)
    requests.get = AsyncMock(return_value=_request())
    requests.transition = AsyncMock(return_value=_request(CodeRequestState.DONE))
    pipelines = PipelineStore(tmp_path / "pipelines.json")
    pipeline = ExecutorPipeline(code_request_id=CR_ID)
    pipeline.children = {"a": _child("a", 11)}
    pipelines.save(pipeline.snapshot())
    monkeypatch.setattr(executor_router, "_get_store", lambda: requests)
    monkeypatch.setattr(executor_router, "_pipeline_store", lambda: PipelineStore(tmp_path / "pipelines.json"))
    monkeypatch.setattr(executor_router, "_pr_probe", lambda: GREEN)
    app.dependency_overrides[require_principal] = lambda: Principal(
        id="op", type="human", name="Op", roles=["operator"], scopes=["code-requests.manage", "code-requests.view"]
    )
    yield {"requests": requests, "path": tmp_path / "pipelines.json"}
    app.dependency_overrides.clear()


def _client() -> TestClient:
    return TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"}, raise_server_exceptions=False)


def _report(client: TestClient, criterion: str, *, passed: bool = True, source: str = "script") -> Any:
    return client.post(
        f"/api/code-requests/{CR_ID}/executor/report-child",
        json={
            "key": "a",
            "event": "acceptance_checked",
            "criterion": criterion,
            "passed": passed,
            "evidence": "pytest tests/test_a.py: 4 passed",
            "source": source,
        },
    )


@pytest.mark.unit
def test_acceptance_checks_are_reported_and_persisted(env: dict[str, Any]) -> None:
    client = _client()
    res = _report(client, "a works")
    assert res.status_code == 200, res.text
    snap = PipelineStore(env["path"]).get(CR_ID)
    assert snap is not None
    [check] = snap.children["a"].acceptance_checks
    assert (check.criterion, check.passed, check.recorded_by) == ("a works", True, "op")
    assert _report(client, "not a criterion").status_code == 422
    assert _report(client, "a works", source="vibes").status_code == 422


@pytest.mark.unit
def test_complete_lists_unmet_conditions_and_does_not_transition(env: dict[str, Any]) -> None:
    client = _client()
    _report(client, "a works")
    res = client.post(f"/api/code-requests/{CR_ID}/executor/complete")
    assert res.status_code == 409
    assert res.json()["detail"]["unmet"] == ["child 'a': no recorded acceptance check for 'a is tested'"]
    env["requests"].transition.assert_not_awaited()


@pytest.mark.unit
def test_complete_enters_done_through_the_acceptance_gate(env: dict[str, Any]) -> None:
    client = _client()
    _report(client, "a works")
    _report(client, "a is tested", source="qa-verifier")
    res = client.post(f"/api/code-requests/{CR_ID}/executor/complete")
    assert res.status_code == 200, res.text
    args, kwargs = env["requests"].transition.await_args
    assert args[:2] == (CR_ID, CodeRequestState.DONE)
    assert kwargs["gate"] is TransitionGate.ACCEPTANCE and kwargs["actor"] == "op"


@pytest.mark.unit
def test_complete_needs_an_executing_request(env: dict[str, Any]) -> None:
    env["requests"].get = AsyncMock(return_value=_request(CodeRequestState.PLANNED))
    res = _client().post(f"/api/code-requests/{CR_ID}/executor/complete")
    assert res.status_code == 409 and "executing" in res.text
    env["requests"].transition.assert_not_awaited()
