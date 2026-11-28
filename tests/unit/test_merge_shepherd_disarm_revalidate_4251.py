"""#4251: a shepherd-planned disarm must execute for every disarm reason, not just one.

Measured on `main` 2026-11-28: the scheduled Merge shepherd pass failed more than 20
consecutive runs (e.g. run 46124351262 at 08:09Z) with
``{"pr": 5781, "detail": "incomplete_comment_history", "error": "disarm revalidation failed"}``
while AffineDrift#5781 stayed auto-merge armed. ``_revalidate_disarm`` compared the
planned row against one reason string, and ``disarm_for_shepherd`` only pulled
auto-merge for that same reason or a hold, so every requeue-suppression reason was
unexecutable twice over.
"""

from __future__ import annotations

from typing import Any

import pytest

import scripts.merge_shepherd_pass as msp
from scripts import automerge_guard
from tests.unit.test_automerge_guard_disarm_route_4251 import LiveGitHubGh

HEAD = "d" * 40
REPO = "D-sorganization/AffineDrift"


def _suppression_node(*, number: int = 5781) -> dict[str, Any]:
    """An armed PR whose only disarm reason is ``incomplete_comment_history``.

    No review verdict is bound to the head, so ``pass_at_head`` does not produce
    ``rejected_at_head``; a newer comment page exists with no authorized retry, so
    ``suppression_detail`` fails closed on comment history instead.
    """
    return {
        "number": number,
        "isDraft": False,
        "headRefOid": HEAD,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "reviewDecision": "APPROVED",
        "author": {"login": "d-sorggeminiagent[bot]"},
        "autoMergeRequest": {"merge_method": "squash"},
        "commits": {
            "nodes": [
                {
                    "commit": {
                        "committedDate": "2026-11-28T06:00:00Z",
                        "statusCheckRollup": {"state": "SUCCESS"},
                    }
                }
            ],
            "pageInfo": {"hasNextPage": False},
        },
        "labels": {"nodes": []},
        "files": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [{"path": "docs/protocol.md", "additions": 4, "deletions": 2}],
        },
        "reviews": {"nodes": []},
        "timelineItems": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        "comments": {
            "nodes": [
                {
                    "createdAt": "2026-11-28T05:00:00Z",
                    "body": "Triggering the E2E suite for additional verification.",
                    "author": {"login": "d-sorgcodexagent[bot]"},
                }
            ],
            "pageInfo": {"hasNextPage": True},
        },
    }


def _plan_disarms(node: dict[str, Any]) -> list[Any]:
    return msp.plan_repo_disarms(REPO, [node], skip_list=frozenset(), holds=(), live=True, review_passes=())


def _patch_graphql(monkeypatch: pytest.MonkeyPatch, node: dict[str, Any]) -> None:
    monkeypatch.setattr(msp, "_graphql", lambda *args: {})
    monkeypatch.setattr(msp, "graphql_nodes", lambda payload: ([node], 1, 0))
    monkeypatch.setattr(msp, "resolve_merge_shepherd_review_store", lambda **kw: None)


def _guard_module() -> Any:
    import importlib
    import sys

    scripts_dir = str(msp.Path(__file__).resolve().parents[2] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    return importlib.import_module("automerge_guard")


def test_premise_the_suppression_reason_is_not_rejected_at_head() -> None:
    """Locks the fixture onto the production reason for AffineDrift#5781."""
    disarms = _plan_disarms(_suppression_node())
    assert [act.detail for act in disarms] == ["incomplete_comment_history"]


def test_revalidate_accepts_a_disarm_planned_for_a_requeue_suppression_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = _suppression_node()
    _patch_graphql(monkeypatch, node)
    assert msp._revalidate_disarm(repo=REPO, pr=5781, head_sha=HEAD, repo_root=msp.Path.cwd()) is True


def test_revalidate_still_refuses_a_moved_head(monkeypatch: pytest.MonkeyPatch) -> None:
    node = _suppression_node()
    _patch_graphql(monkeypatch, node)
    assert msp._revalidate_disarm(repo=REPO, pr=5781, head_sha="b" * 40, repo_root=msp.Path.cwd()) is False


def test_revalidate_refuses_when_nothing_is_planned_to_disarm(monkeypatch: pytest.MonkeyPatch) -> None:
    node = _suppression_node()
    node["autoMergeRequest"] = None
    node["comments"]["pageInfo"]["hasNextPage"] = False
    _patch_graphql(monkeypatch, node)
    assert msp._revalidate_disarm(repo=REPO, pr=5781, head_sha=HEAD, repo_root=msp.Path.cwd()) is False


def test_disarm_via_guard_executes_a_requeue_suppression_disarm(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole shepherd path: plan -> revalidate -> disarm must clear the arm."""
    node = _suppression_node()
    fake = LiveGitHubGh(auto_merge_on=True, in_merge_queue=False)
    _patch_graphql(monkeypatch, node)
    guard_mod = _guard_module()
    monkeypatch.setattr(automerge_guard, "check_token_identity", lambda **kw: True)
    monkeypatch.setattr(
        guard_mod,
        "disarm_for_shepherd",
        lambda *args, **kw: automerge_guard.disarm_for_shepherd(*args, **kw, runner=fake),
    )
    result = msp._disarm_via_guard(
        REPO,
        5781,
        repo_root=msp.Path.cwd(),
        head_sha=HEAD,
        detail="incomplete_comment_history",
    )
    assert result.get("error") is None, result
    assert result["disarmed"] is True
    assert result["exit_code"] == 0
    assert fake.cli_disarm_calls == 1
    assert result["after_auto_merge"] is False


def test_rejected_at_head_disarm_still_clears_the_queue_only_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a queued PR with no arm set is still dequeued at rejected_at_head."""
    node = _suppression_node()
    _patch_graphql(monkeypatch, node)
    fake = LiveGitHubGh(auto_merge_on=False, in_merge_queue=True)
    monkeypatch.setattr(automerge_guard, "check_token_identity", lambda **kw: True)
    before, after, _verdict = automerge_guard.disarm_for_shepherd(
        "o/r",
        7,
        detail="rejected_at_head",
        head_sha="a" * 40,
        revalidate=lambda: True,
        runner=fake,
    )
    assert before.in_merge_queue is True
    assert automerge_guard.disposition_cleared(after) is True


def test_hold_disarm_still_marks_with_the_hold_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a held PR is disarmed and annotated with the hold, not the plan detail."""

    class _Verdict:
        held = True

        @staticmethod
        def describe() -> str:
            return "hold: do-not-merge label"

    fake = LiveGitHubGh(auto_merge_on=True, in_merge_queue=False)
    monkeypatch.setattr(automerge_guard, "check_token_identity", lambda **kw: True)
    monkeypatch.setattr(automerge_guard, "evaluate_hold", lambda *a, **kw: _Verdict())
    _before, after, verdict = automerge_guard.disarm_for_shepherd(
        "o/r",
        7,
        detail="incomplete_comment_history",
        head_sha="a" * 40,
        revalidate=lambda: True,
        runner=fake,
    )
    assert verdict.held is True
    assert automerge_guard.disposition_cleared(after) is True
    assert any("do-not-merge label" in body for body in fake.comments)
