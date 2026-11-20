"""Replay the box's last 7 days and require identical arm decisions (Refs #4249).

``tests/fixtures/review_arm_replay_7d.json`` is a sanitized trim of the box's
ledger.jsonl / armed.txt (2026-11-10..17): every review whose verdict is
recoverable, in order, with the arm attempt armd made and the guard outcome.
The replay drives the real ledger, verdict processor, arm gate and arm-only
armer (against a fake GitHub built from the recorded PR state) and requires:

- an arm attempt exactly where armd attempted one;
- auto-merge armed exactly where the box's arm succeeded (deletion refusals,
  RD #4449 style, and GitHub-side refusals stay unarmed).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from staff.review_arm.armer import ArmOutcome, GitHubArmer
from staff.review_arm.ledger import Ledger
from staff.review_arm.policy import LiveHead
from staff.review_arm.processor import VerdictProcessor
from staff.review_arm.verdicts import RunOutcome

FIXTURE = Path(__file__).parent / "fixtures" / "review_arm_replay_7d.json"


class ReplayGitHub:
    """GitHub as armd saw it for one event: PR state, deleted files, arm result."""

    def __init__(self, event: dict[str, Any]) -> None:
        self.event = event
        self.mutations: list[str] = []

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        live = self.event["live"]
        if query.lstrip().startswith("mutation"):
            self.mutations.append(query)
            assert "enablePullRequestAutoMerge" in query and "mergePullRequest" not in query
            if self.event["box_guard"] == "refused":
                raise OSError("GitHub refused to enable auto-merge")
            auto = {"enabledAt": "now", "mergeMethod": "SQUASH"}
            return {
                "enablePullRequestAutoMerge": {"pullRequest": {"headRefOid": live["head"], "autoMergeRequest": auto}}
            }
        node = {
            "id": "PR_replay",
            "number": self.event["pr"],
            "headRefOid": live["head"],
            "state": live["state"],
            "isDraft": live["draft"],
            "title": "replay",
            "body": "",
            "labels": {"pageInfo": {"hasNextPage": False}, "nodes": [{"name": n} for n in live["labels"]]},
            "autoMergeRequest": None,
        }
        return {"repository": {"pullRequest": node}}

    def rest_get(self, path: str) -> Any:
        if "page=1" in path and self.event["box_guard"] == "refused_deletions":
            return [{"filename": "changes/deleted-fragment.md", "status": "removed"}]
        return []


class ReplayPort:
    def __init__(self) -> None:
        self.event: dict[str, Any] = {}
        self.github = ReplayGitHub({})

    def load(self, event: dict[str, Any]) -> None:
        self.event = event
        self.github = ReplayGitHub(event)

    def run_outcome(self, run_id: str) -> RunOutcome:
        e = self.event
        assert run_id == e["run"]
        return RunOutcome("verdict", e["verdict"], e["blocking"], e["recovered"], "", "replayed review text")

    def live(self, repo: str, pr: int) -> LiveHead:
        live = self.event["live"]
        return LiveHead(live["head"], live["state"], live["draft"], tuple(live["labels"]))

    def arm(self, repo: str, pr: int, head: str, *, holds: frozenset[tuple[str, int]]) -> ArmOutcome:
        armer = GitHubArmer(graphql=self.github.graphql, rest_get=self.github.rest_get)
        return armer.arm(repo, pr, head, holds=holds)

    def post_findings(self, *_a: Any) -> None:
        pass

    def escalate(self, *_a: Any) -> None:
        pass


@pytest.fixture(scope="module")
def fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_is_sanitized_and_covers_the_window(fixture: dict[str, Any]) -> None:
    raw = FIXTURE.read_text(encoding="utf-8").lower()
    for marker in ("ghp_", "ghs_", "github_pat_", "gho_", "token", "password", "secret", "@gmail"):
        assert marker not in raw
    events = fixture["events"]
    assert len(events) >= 200
    assert {e["box_guard"] for e in events} >= {"armed", "refused", "refused_deletions", None}
    assert sum(e["box_arm_attempt"] for e in events) >= 150


def test_replay_matches_box_arm_decisions(fixture: dict[str, Any], tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "replay.sqlite3")
    ledger.import_box_state(
        holds=fixture["arm_holds"], blocked=fixture["blocked_heads_before"], overrides=fixture["arm_override"]
    )
    port = ReplayPort()
    processor = VerdictProcessor(ledger, port, dry_run=False)
    mismatches: list[str] = []
    stricter: set[str] = set()
    for event in fixture["events"]:
        ledger.record_dispatch(
            event["repo"], event["pr"], event["head"], run_id=event["run"], round_no=event["round"], provider="claude"
        )
        port.load(event)
        [dispatch] = ledger.pending_dispatches()
        result = processor.process(dispatch)
        assert result is not None
        attempted = bool(port.github.mutations) or result.decision.action == "arm"
        armed_expected = event["box_guard"] == "armed"
        # Stricter, same result: armd only knew its static HOLD set, so it attempted
        # seed-held PRs and the guard refused them; RD-native refuses at the gate.
        held_early = event["box_guard"] == "refused" and result.decision.reason in {"held_pr", "hold_label"}
        if held_early:
            stricter.add(f"{event['repo']}#{event['pr']}")
        if (attempted != event["box_arm_attempt"] and not held_early) or result.armed != armed_expected:
            mismatches.append(
                f"#{event['seq']} {event['repo']}#{event['pr']}@{event['head'][:8]} {event['verdict']}"
                f" b={event['blocking']}: box attempt={event['box_arm_attempt']} guard={event['box_guard']}"
                f" | ours {result.decision.action}/{result.decision.reason} armed={result.armed}"
            )
    assert mismatches == [], "\n".join(mismatches)
    assert stricter == {"Gasification_Model#5824", "Gasification_Model#5735"}


def test_replay_never_sends_a_merge(fixture: dict[str, Any], tmp_path: Path) -> None:
    """Every mutation across the replay is the arm-only enablePullRequestAutoMerge."""
    ledger = Ledger(tmp_path / "replay.sqlite3")
    port = ReplayPort()
    processor = VerdictProcessor(ledger, port, dry_run=False)
    sent: list[str] = []
    for event in fixture["events"][:60]:
        ledger.record_dispatch(event["repo"], event["pr"], event["head"], run_id=event["run"], round_no=1, provider="x")
        port.load(event)
        processor.process(ledger.pending_dispatches()[0])
        sent += port.github.mutations
    forbidden = ("mergepullrequest", "enqueuepullrequest", "mergebranch")
    assert sent and all("enablePullRequestAutoMerge" in q and not any(f in q.lower() for f in forbidden) for q in sent)
