"""RDHubPort: required checks, prompt, context and an end-to-end dry run (Refs #4249)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from staff.review_arm.armer import GitHubArmer
from staff.review_arm.hub_port import CHECKS_QUERY, RDHubPort, required_checks_green, review_prompt
from staff.review_arm.ledger import Ledger
from staff.review_arm.policy import Candidate
from staff.review_arm.processor import VerdictProcessor
from staff.review_arm.service import ReviewArmService
from staff.review_arm.settings import Settings

HEAD = "a" * 40
APPROVED = "c" * 40


def _checks(nodes: list[dict[str, Any]], *, truncated: bool = False, oid: str = HEAD) -> dict[str, Any]:
    rollup = {"contexts": {"pageInfo": {"hasNextPage": truncated}, "nodes": nodes}}
    return {
        "repository": {"pullRequest": {"commits": {"nodes": [{"commit": {"oid": oid, "statusCheckRollup": rollup}}]}}}
    }


RUN_OK = {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS", "isRequired": True}


@pytest.mark.parametrize(
    ("nodes", "truncated", "green"),
    [
        ([RUN_OK], False, True),
        ([RUN_OK, {"__typename": "StatusContext", "state": "SUCCESS", "isRequired": True}], False, True),
        ([RUN_OK, {**RUN_OK, "status": "IN_PROGRESS", "conclusion": None}], False, False),
        ([RUN_OK, {**RUN_OK, "conclusion": "FAILURE"}], False, False),
        ([{**RUN_OK, "isRequired": False}], False, False),  # no required check at all
        ([{**RUN_OK, "conclusion": "FAILURE", "isRequired": False}, RUN_OK], False, True),  # optional failure
        ([RUN_OK], True, False),
    ],
)
def test_required_checks_green(nodes: list[dict[str, Any]], truncated: bool, green: bool) -> None:
    assert required_checks_green(_checks(nodes, truncated=truncated)) == (green, HEAD)


def test_review_prompt_pins_head_and_policy2_contract() -> None:
    c = Candidate("Tools", 7, HEAD)
    first, later = review_prompt(c, 1), review_prompt(c, 3)
    assert HEAD in first and "@aaaaaaa" in first and "[BLOCKING]" in first
    assert "Auto-intake round 3" in later and "Delta review" in later


class FakeStore:
    def list_runs_for_target(self, **_kw: Any) -> list[Any]:
        return []

    def get_run(self, run_id: str) -> None:
        return None

    def events_after(self, *_a: Any) -> list[Any]:
        return []


class FakeGitHub:
    """Serves the list, checks and PR queries; records anything else."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _node(self, number: int, head: str) -> dict[str, Any]:
        return {
            "number": number,
            "headRefOid": head,
            "title": "fix",
            "isDraft": False,
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "labels": {"pageInfo": {"hasNextPage": False}, "nodes": []},
            "autoMergeRequest": None,
            "mergeQueueEntry": None,
        }

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(query.split("(")[0].strip())
        if query.lstrip().startswith("mutation"):
            raise AssertionError("dry-run sent a GitHub mutation")
        if query == CHECKS_QUERY:
            head = APPROVED if variables["number"] == 2 else HEAD
            return _checks([RUN_OK], oid=head)
        if "pullRequests(states: OPEN" in query:
            nodes = [self._node(1, HEAD), self._node(2, APPROVED)]
            return {"repository": {"pullRequests": {"pageInfo": {"hasNextPage": False}, "nodes": nodes}}}
        number = variables["number"]
        head = APPROVED if number == 2 else HEAD
        node = self._node(number, head) | {"id": f"PR_{number}", "state": "OPEN", "body": ""}
        return {"repository": {"pullRequest": node}}

    def rest_get(self, path: str) -> Any:
        self.calls.append(f"GET {path}")
        return []


@pytest.fixture
def port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[RDHubPort, FakeGitHub]:
    import gh_client

    from staff import dispatch_service, review

    async def no_write(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("dry-run made a GitHub REST write or dispatch")

    monkeypatch.setattr(gh_client, "post", no_write)
    monkeypatch.setattr(dispatch_service, "dispatch_staff_run", no_write)
    monkeypatch.setattr(review, "detect_author_provider", lambda *a, **k: "cursor-agent")
    settings = Settings(enabled=True, repos=("Tools",), ledger_path=tmp_path / "l.sqlite3")
    ledger = Ledger(settings.ledger_path)
    loop = asyncio.new_event_loop()
    notes: list[tuple[str, ...]] = []
    hub = RDHubPort(settings=settings, ledger=ledger, loop=loop, store=FakeStore(), notify=lambda *a: notes.append(a))
    fake = FakeGitHub()
    hub.graphql = fake.graphql  # type: ignore[method-assign]
    hub.rest_get = fake.rest_get  # type: ignore[method-assign]
    hub.armer = GitHubArmer(graphql=fake.graphql, rest_get=fake.rest_get)
    yield hub, fake
    loop.close()


def test_context_reflects_ledger_state(port: tuple[RDHubPort, FakeGitHub]) -> None:
    hub, _fake = port
    hub.ledger.record_dispatch("Tools", 7, HEAD, run_id="run-1", round_no=1, provider="claude")
    c = hub._context(Candidate("Tools", 7, HEAD))
    assert (c.queued, c.reviewed, c.prior_runs, c.holds_available) == (True, False, 1, True)


def test_end_to_end_dry_run_makes_no_writes_and_no_dispatch(port: tuple[RDHubPort, FakeGitHub]) -> None:
    hub, fake = port
    from staff.review_arm.ledger import Verdict

    hub.ledger.ingest(Verdict("Tools", 2, APPROVED, "approve", "claude", "run-0", "m", summary="ok a.py:1"))
    processor = VerdictProcessor(hub.ledger, hub, dry_run=True)  # type: ignore[arg-type]
    service = ReviewArmService(hub, enabled=True, dry_run=True, processor=processor, held_prs=frozenset())
    service.cycle()
    status = service.status()
    assert status["counts"] == {"review": 1, "arm": 1}, status
    assert not any(call.startswith("mutation") for call in fake.calls)
    assert hub.ledger.pending_dispatches() == []
