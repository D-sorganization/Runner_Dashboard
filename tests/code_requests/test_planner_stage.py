"""CR-4 (#1285) end to end with a mocked planner provider and a fake GitHub.

prompt -> invalid plan -> re-prompt (with the validator errors) -> valid plan -> draft
(no GitHub writes) -> approval -> epic and children filed, turnover comments posted,
children linked as sub-issues, Code Request moved to ``planned`` with ``plan_epic``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from code_requests.model import CodeRequest, CodeRequestState, Requester  # noqa: E402
from code_requests.plan_service import (  # noqa: E402
    PlanDeps,
    PlanningError,
    approve_plan,
    edit_draft,
    ingest_plan_comment,
    start_planning,
    submit_plan,
)
from code_requests.plan_store import PlanSessionStore  # noqa: E402
from code_requests.planner import MAX_REPROMPTS, PLAN_COMMENT_MARKER, PlanningStatus  # noqa: E402
from code_requests.profiles import AgentProfileStore  # noqa: E402
from code_requests.store import CodeRequestStore  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_plan_validator import good_plan  # noqa: E402

ORG = "D-sorganization"
REPO = "Runner_Dashboard"


class FakeGitHub:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str, Any]] = []
        self.comments: list[dict[str, Any]] = []
        self._next = 500

    async def fetch(self, endpoint: str) -> Any:
        if endpoint.endswith("/contents/AGENTS.md"):
            raise RuntimeError("404")
        if endpoint.endswith("/contents/CLAUDE.md"):
            return {"encoding": "base64", "content": base64.b64encode(b"Rule: always TDD.").decode()}
        if endpoint.startswith("/search/issues"):
            return {"items": [{"number": 12, "title": "Old widget idea"}, {"number": 31, "title": "Widget cache"}]}
        if "/commits/" in endpoint:
            return {"sha": "95ba642aa1bb2cc3dd4ee5ff"}  # pragma: allowlist secret (fake commit SHA)
        if endpoint.endswith("/comments?per_page=100"):
            return self.comments
        raise AssertionError(f"unexpected fetch {endpoint}")

    async def write(self, endpoint: str, method: str = "POST", json_body: Any = None) -> Any:
        self.writes.append((endpoint, method, json_body))
        if endpoint.endswith("/issues") and method == "POST":
            self._next += 1
            return {
                "number": self._next,
                "id": 9000 + self._next,
                "html_url": f"https://github.com/{ORG}/{REPO}/issues/{self._next}",
            }
        return {}

    def filing_writes(self) -> list[tuple[str, str, Any]]:
        """Writes other than the Code Request's own audit comments and body updates."""
        return [w for w in self.writes if "/issues/42" not in w[0]]


class FakePlanner:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def __call__(self, repo: str, branch: str, provider: str, prompt: str, **_: Any) -> tuple[int, str]:
        self.prompts.append(prompt)
        return 0, ""


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[PlanDeps, FakeGitHub, FakePlanner]:
    # Transitions append to the node's dispatch audit log; keep that out of the home directory.
    monkeypatch.setattr("code_requests.store.record_code_request_transition", lambda entry: None)
    gh, planner = FakeGitHub(), FakePlanner()
    request = CodeRequest(
        id="cr-plan-1",
        repository=REPO,
        title="Code Request: widget pipeline",
        issue_number=42,
        issue_url=f"https://github.com/{ORG}/{REPO}/issues/42",
        state=CodeRequestState.PLANNING,
        prompt="Build the widget pipeline.",
        requester=Requester(id="owner"),
        created_at="2026-09-25T00:00:00+00:00",
        updated_at="2026-09-25T00:00:00+00:00",
    )
    cache = tmp_path / "code_requests.json"
    cache.write_text(json.dumps([request.model_dump(mode="json")]), encoding="utf-8")
    deps = PlanDeps(
        requests=CodeRequestStore(cache_path=cache, org=ORG, fetch_fn=gh.fetch, write_fn=gh.write),
        profiles=AgentProfileStore(tmp_path / "profiles.json"),
        sessions=PlanSessionStore(tmp_path / "plans.json"),
        org=ORG,
        fetch=gh.fetch,
        write=gh.write,
        dispatch=planner,
    )
    return deps, gh, planner


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def bad_plan() -> str:
    plan = good_plan()
    del plan["children"][0]["tier"]
    return json.dumps(plan)


def test_invalid_plan_is_reprompted_then_a_valid_plan_is_filed_only_after_approval(env: Any) -> None:
    deps, gh, planner = env
    session = run(start_planning(deps, "cr-plan-1", principal="owner"))
    assert session.attempts == 1 and session.status is PlanningStatus.AWAITING_PLAN
    assert "Rule: always TDD." in planner.prompts[0]
    assert PLAN_COMMENT_MARKER in planner.prompts[0]

    session = run(submit_plan(deps, "cr-plan-1", bad_plan(), actor="planner-bot"))
    assert session.attempts == 2 and session.status is PlanningStatus.AWAITING_PLAN
    assert "rejected" in planner.prompts[1] and "tier" in planner.prompts[1]

    session = run(submit_plan(deps, "cr-plan-1", "```json\n" + json.dumps(good_plan()) + "\n```", actor="planner-bot"))
    assert session.status is PlanningStatus.DRAFT
    assert gh.writes == [], "nothing may be written to GitHub before approval"

    session = run(approve_plan(deps, "cr-plan-1", actor="owner"))
    assert session.status is PlanningStatus.FILED
    writes = gh.filing_writes()
    created = [w for w in writes if w[0].endswith("/issues") and w[1] == "POST"]
    assert [w[2]["title"] for w in created] == ["Epic: widget pipeline", "Implement a", "Implement b", "Implement c"]
    epic_body = created[0][2]["body"]
    assert "#31 Widget cache" in epic_body and "#12 Old widget idea" in epic_body
    assert "tier:cli" in created[1][2]["labels"] and "feature" in created[1][2]["labels"]
    assert "#502" in created[2][2]["body"], "b's dependency on a resolves to a's issue number"

    turnovers = [w for w in writes if w[0].endswith("/comments")]
    assert len(turnovers) == 3
    assert all(t[2]["body"].startswith("<!-- turnover:v1 -->") for t in turnovers)
    assert "Baseline commit: 95ba642aa1bb" in turnovers[0][2]["body"]
    links = [w for w in writes if w[0].endswith("/issues/501/sub_issues")]
    assert [w[2]["sub_issue_id"] for w in links] == [9502, 9503, 9504]

    request = run(deps.requests.get("cr-plan-1"))
    assert request.state is CodeRequestState.PLANNED
    assert request.plan_epic == f"https://github.com/{ORG}/{REPO}/issues/501"


def test_a_plan_rejected_after_every_retry_fails_the_request(env: Any) -> None:
    deps, gh, planner = env
    run(start_planning(deps, "cr-plan-1", principal="owner"))
    for _ in range(MAX_REPROMPTS + 1):
        session = run(submit_plan(deps, "cr-plan-1", bad_plan(), actor="planner-bot"))
    assert session.status is PlanningStatus.FAILED
    assert len(planner.prompts) == 1 + MAX_REPROMPTS
    request = run(deps.requests.get("cr-plan-1"))
    assert request.state is CodeRequestState.FAILED
    assert "tier" in request.audit_trail[-1].reason
    assert not [w for w in gh.filing_writes() if w[0].endswith("/issues")]


def test_the_plan_can_arrive_as_an_issue_comment_and_is_ingested_once(env: Any) -> None:
    deps, gh, _ = env
    run(start_planning(deps, "cr-plan-1", principal="owner"))
    gh.comments = [
        {"id": 1, "body": "unrelated"},
        {"id": 2, "body": f"{PLAN_COMMENT_MARKER}\n```json\n{json.dumps(good_plan())}\n```"},
    ]
    assert run(ingest_plan_comment(deps, "cr-plan-1", actor="owner")).status is PlanningStatus.DRAFT
    with pytest.raises(PlanningError) as exc:
        run(ingest_plan_comment(deps, "cr-plan-1", actor="owner"))
    assert exc.value.status == 409


def test_an_operator_edit_is_revalidated(env: Any) -> None:
    deps, _, _ = env
    run(start_planning(deps, "cr-plan-1", principal="owner"))
    run(submit_plan(deps, "cr-plan-1", json.dumps(good_plan()), actor="planner-bot"))
    edited = good_plan()
    edited["children"][0]["title"] = "Implement a, renamed"
    assert run(edit_draft(deps, "cr-plan-1", edited)).draft.children[0].title == "Implement a, renamed"
    edited["children"][0]["dependencies"] = ["c"]
    with pytest.raises(PlanningError) as exc:
        run(edit_draft(deps, "cr-plan-1", edited))
    assert exc.value.status == 422 and "cycle" in exc.value.detail.lower()


def test_a_profile_without_the_approval_gate_files_immediately(env: Any) -> None:
    deps, gh, _ = env
    deps.profiles.update("planner-strong", {"approval_gates": {"plan_requires_approval": False}})
    run(start_planning(deps, "cr-plan-1", principal="owner"))
    session = run(submit_plan(deps, "cr-plan-1", json.dumps(good_plan()), actor="planner-bot"))
    assert session.status is PlanningStatus.FILED
    assert gh.filing_writes()


def test_an_interrupted_filing_resumes_without_duplicates(env: Any) -> None:
    deps, gh, _ = env
    run(start_planning(deps, "cr-plan-1", principal="owner"))
    run(submit_plan(deps, "cr-plan-1", json.dumps(good_plan()), actor="planner-bot"))
    real_write, calls = gh.write, {"n": 0}

    async def flaky(endpoint: str, method: str = "POST", json_body: Any = None) -> Any:
        calls["n"] += 1
        if calls["n"] == 4:
            raise RuntimeError("GitHub 502")
        return await real_write(endpoint, method, json_body)

    deps_flaky = PlanDeps(**{**deps.__dict__, "write": flaky})
    with pytest.raises(RuntimeError):
        run(approve_plan(deps_flaky, "cr-plan-1", actor="owner"))
    run(approve_plan(deps, "cr-plan-1", actor="owner"))
    created = [w for w in gh.filing_writes() if w[0].endswith("/issues") and w[1] == "POST"]
    assert len(created) == 4, [w[2]["title"] for w in created]


@pytest.mark.parametrize("state", [CodeRequestState.TRIAGE, CodeRequestState.PLANNED])
def test_planning_starts_only_from_the_planning_state(env: Any, state: CodeRequestState) -> None:
    deps, _, planner = env
    request = run(deps.requests.get("cr-plan-1"))
    deps.requests._write_cache([request.model_copy(update={"state": state})])
    with pytest.raises(PlanningError) as exc:
        run(start_planning(deps, "cr-plan-1", principal="owner"))
    assert exc.value.status == 409
    assert planner.prompts == []
