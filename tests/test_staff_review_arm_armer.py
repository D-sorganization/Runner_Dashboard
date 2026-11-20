"""Arm-only GitHub capability: enablePullRequestAutoMerge, never a merge (Refs #4249)."""

from __future__ import annotations

from typing import Any

import pytest
from staff.review_arm.armer import ARM_MUTATION, GitHubArmer

HEAD = "a" * 40
NEW = "b" * 40
FORBIDDEN = ("mergePullRequest", "enqueuePullRequest", "mergeBranch", "/merge", "admin")


class FakeGitHub:
    """Records every request; serves one PR and its file list."""

    def __init__(
        self,
        *,
        head: str = HEAD,
        state: str = "OPEN",
        draft: bool = False,
        labels: tuple[str, ...] = (),
        body: str = "",
        removed: tuple[str, ...] = (),
        armed: bool = False,
        labels_truncated: bool = False,
        arm_result_head: str = HEAD,
        arm_error: bool = False,
    ) -> None:
        self.pr = {
            "id": "PR_node",
            "number": 7,
            "headRefOid": head,
            "state": state,
            "isDraft": draft,
            "title": "fix: thing",
            "body": body,
            "labels": {"pageInfo": {"hasNextPage": labels_truncated}, "nodes": [{"name": n} for n in labels]},
            "autoMergeRequest": {"enabledAt": "x"} if armed else None,
        }
        self.removed = removed
        self.arm_result_head = arm_result_head
        self.arm_error = arm_error
        self.graphql_calls: list[tuple[str, dict[str, Any]]] = []
        self.rest_calls: list[str] = []

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        self.graphql_calls.append((query, variables))
        if query.lstrip().startswith("mutation"):
            if self.arm_error:
                raise OSError("GitHub refused")
            return {
                "enablePullRequestAutoMerge": {
                    "pullRequest": {
                        "headRefOid": self.arm_result_head,
                        "autoMergeRequest": {"enabledAt": "now", "mergeMethod": "SQUASH"},
                    }
                }
            }
        return {"repository": {"pullRequest": self.pr}}

    def rest_get(self, path: str) -> Any:
        self.rest_calls.append(path)
        page = int(path.rsplit("page=", 1)[1])
        files = [{"filename": f, "status": "removed"} for f in self.removed]
        files.append({"filename": "a.py", "status": "modified"})
        return files if page == 1 else []

    @property
    def mutations(self) -> list[tuple[str, dict[str, Any]]]:
        return [c for c in self.graphql_calls if c[0].lstrip().startswith("mutation")]

    def assert_never_merged(self) -> None:
        for query, _vars in self.mutations:
            assert "enablePullRequestAutoMerge" in query
            assert not any(word in query for word in FORBIDDEN)
        for path in self.rest_calls:
            assert "/merge" not in path


def _armer(gh: FakeGitHub) -> GitHubArmer:
    return GitHubArmer(graphql=gh.graphql, rest_get=gh.rest_get)


def test_arms_with_enable_auto_merge_pinned_to_head() -> None:
    gh = FakeGitHub()
    out = _armer(gh).arm("Tools", 7, HEAD, holds=frozenset())
    assert out.armed is True
    assert len(gh.mutations) == 1
    query, variables = gh.mutations[0]
    assert query == ARM_MUTATION
    assert variables == {"id": "PR_node", "oid": HEAD}
    assert "SQUASH" in query
    gh.assert_never_merged()


def test_mutation_text_is_arm_only() -> None:
    assert "enablePullRequestAutoMerge" in ARM_MUTATION
    assert "expectedHeadOid" in ARM_MUTATION
    assert not any(word in ARM_MUTATION for word in FORBIDDEN)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"head": NEW}, "head moved"),
        ({"state": "MERGED"}, "not open"),
        ({"draft": True}, "draft"),
        ({"labels": ("do-not-merge",)}, "hold_label"),
        ({"labels_truncated": True}, "labels incomplete"),
        ({"removed": ("docs/old.md",)}, "deletes 1 tracked file"),
    ],
)
def test_refusals_make_no_mutation(kwargs: dict[str, Any], reason: str) -> None:
    gh = FakeGitHub(**kwargs)
    out = _armer(gh).arm("Tools", 7, HEAD, holds=frozenset())
    assert out.armed is False
    assert reason in out.detail
    assert gh.mutations == []


def test_held_pr_is_refused() -> None:
    gh = FakeGitHub()
    out = _armer(gh).arm("Tools", 7, HEAD, holds=frozenset({("Tools", 7)}))
    assert (out.armed, out.detail) == (False, "held_pr")
    assert gh.mutations == []


def test_issue_4449_style_deletion_refused_unless_acknowledged() -> None:
    removed = ("articles/a.md", "articles/b.jsx")
    assert _armer(FakeGitHub(removed=removed)).arm("AffineDrift", 7, HEAD, holds=frozenset()).armed is False
    by_label = FakeGitHub(removed=removed, labels=("deletions-acknowledged",))
    assert _armer(by_label).arm("AffineDrift", 7, HEAD, holds=frozenset()).armed is True
    by_body = FakeGitHub(removed=removed, body="Summary\ndeletions-acknowledged: yes\n")
    assert _armer(by_body).arm("AffineDrift", 7, HEAD, holds=frozenset()).armed is True


def test_already_armed_is_not_rearmed() -> None:
    gh = FakeGitHub(armed=True)
    out = _armer(gh).arm("Tools", 7, HEAD, holds=frozenset())
    assert (out.armed, out.detail) == (True, "already armed")
    assert gh.mutations == []


def test_arm_error_is_reported_not_retried_or_merged() -> None:
    gh = FakeGitHub(arm_error=True)
    out = _armer(gh).arm("Tools", 7, HEAD, holds=frozenset())
    assert out.armed is False
    assert len(gh.mutations) == 1
    gh.assert_never_merged()


def test_arm_result_at_other_head_is_not_reported_armed() -> None:
    gh = FakeGitHub(arm_result_head=NEW)
    assert _armer(gh).arm("Tools", 7, HEAD, holds=frozenset()).armed is False


def test_live_head_reads_pr_state() -> None:
    live = _armer(FakeGitHub(labels=("x",), draft=True)).live("Tools", 7)
    assert (live.head_sha, live.state, live.draft, live.labels) == (HEAD, "OPEN", True, ("x",))


def test_live_head_with_truncated_labels_fails_closed() -> None:
    with pytest.raises(OSError):
        _armer(FakeGitHub(labels_truncated=True)).live("Tools", 7)


def test_armer_module_has_no_merge_capability() -> None:
    import inspect

    from staff.review_arm import armer

    source = inspect.getsource(armer)
    assert "mergePullRequest" not in source
    assert "enqueuePullRequest" not in source
    assert "pulls/{pr}/merge" not in source
    assert "arm_auto_merge" not in source
