from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from coordination import pr_age_rule as rule

# Relative to "now" so these fixtures never bit-rot into a flaky time bomb as
# the real clock advances past a hardcoded absolute date (#3953 CI-unblock).
_OLD_PR_CREATED_AT = (datetime.now(UTC) - timedelta(hours=360)).strftime("%Y-%m-%dT%H:%M:%SZ")
_FRESH_PR_CREATED_AT = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
_VERY_OLD_PR_CREATED_AT = (datetime.now(UTC) - timedelta(hours=800)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.unit
def test_resolve_pr_owner_from_branch_prefix() -> None:
    pr = {"head": {"ref": "cursor/flow-metrics-5904"}, "labels": []}
    owner = rule.resolve_pr_owner(pr, agent_ids=["cursor", "codex"])
    assert owner == "cursor"


@pytest.mark.unit
def test_list_aging_prs_for_agent_filters_by_age() -> None:
    def fetch(path: str) -> list[dict]:
        if path.endswith("/pulls?state=open&per_page=100") or path.endswith("/pulls?state=open"):
            return [
                {
                    "number": 1,
                    "html_url": "https://github.com/D-sorganization/Runner_Dashboard/pull/1",
                    "created_at": _OLD_PR_CREATED_AT,
                    "head": {"ref": "cursor/old-work"},
                    "labels": [],
                },
                {
                    "number": 2,
                    "html_url": "https://github.com/D-sorganization/Runner_Dashboard/pull/2",
                    "created_at": _FRESH_PR_CREATED_AT,
                    "head": {"ref": "cursor/fresh"},
                    "labels": [],
                },
            ]
        raise AssertionError(path)

    hits = rule.list_aging_prs_for_agent(
        "cursor",
        repos=["Runner_Dashboard"],
        fetch=fetch,
        rule_hours=72,
    )
    assert len(hits) == 1
    assert hits[0]["number"] == 1


@pytest.mark.unit
def test_claim_blocked_payload() -> None:
    block = rule.claim_blocked_by_aging_prs(
        "cursor",
        claim_repo="Runner_Dashboard",
        fetch=lambda path: (
            [
                {
                    "number": 9,
                    "html_url": "https://example/pull/9",
                    "created_at": _VERY_OLD_PR_CREATED_AT,
                    "head": {"ref": "cursor/stale"},
                    "labels": [],
                }
            ]
            if "pulls" in path
            else []
        ),
    )
    assert block is not None
    assert block["code"] == "aging_pr_blocks_claim"
    assert block["aging_prs"][0]["number"] == 9


def _aging_fetch(pulls_by_repo: dict[str, list[dict]], files_by_pr: dict[tuple[str, int], list[str]]):
    """GitHub stub: open PR lists per repo, and changed files per (repo, PR) (#3892)."""

    def fetch(path: str) -> list[dict]:
        for repo, pulls in pulls_by_repo.items():
            open_pulls = (
                f"/repos/D-sorganization/{repo}/pulls?state=open&per_page=100",
                f"/repos/D-sorganization/{repo}/pulls?state=open",
            )
            if path in open_pulls:
                return pulls
            for (files_repo, number), files in files_by_pr.items():
                file_paths = (
                    f"/repos/D-sorganization/{repo}/pulls/{number}/files?per_page=100",
                    f"/repos/D-sorganization/{repo}/pulls/{number}/files",
                )
                if files_repo == repo and path in file_paths:
                    return [{"filename": name} for name in files]
        if "/pulls?state=open" in path:
            return []
        raise AssertionError(path)

    return fetch


def _old_pr(number: int, branch: str = "codex/old-work", draft: bool = True) -> dict:
    return {
        "number": number,
        "html_url": f"https://example/pull/{number}",
        "created_at": "2026-10-01T00:00:00Z",
        "head": {"ref": branch},
        "labels": [],
        "draft": draft,
    }


@pytest.mark.unit
def test_aging_pr_in_another_repo_does_not_block_the_claim() -> None:
    # #3892: an old draft PR in Operations_Manual_and_Training blocked a claim in Repository_Management.
    fetch = _aging_fetch({"Operations_Manual_and_Training": [_old_pr(87)]}, {})

    block = rule.claim_blocked_by_aging_prs(
        "codex",
        claim_repo="Repository_Management",
        claim_paths=["wood-biochar-methanol"],
        fetch=fetch,
    )

    assert block is None


@pytest.mark.unit
def test_same_repo_aging_pr_with_disjoint_paths_does_not_block() -> None:
    fetch = _aging_fetch(
        {"Repository_Management": [_old_pr(87)]},
        {("Repository_Management", 87): ["HANDOFF.md", "procedures/README.md"]},
    )

    block = rule.claim_blocked_by_aging_prs(
        "codex", claim_repo="Repository_Management", claim_paths=["wood-biochar-methanol"], fetch=fetch
    )

    assert block is None


@pytest.mark.unit
def test_same_repo_aging_pr_with_overlapping_paths_blocks_and_names_them() -> None:
    fetch = _aging_fetch(
        {"Repository_Management": [_old_pr(87)]},
        {("Repository_Management", 87): ["wood-biochar-methanol/model.py", "HANDOFF.md"]},
    )

    block = rule.claim_blocked_by_aging_prs(
        "codex", claim_repo="Repository_Management", claim_paths=["wood-biochar-methanol"], fetch=fetch
    )

    assert block is not None
    assert block["code"] == "aging_pr_blocks_claim"
    hit = block["aging_prs"][0]
    assert (hit["repo"], hit["number"]) == ("Repository_Management", 87)
    assert hit["overlapping_paths"] == ["wood-biochar-methanol/model.py"]
    assert "#87" in block["message"]
    assert "Repository_Management" in block["message"]
    assert "wood-biochar-methanol/model.py" in block["message"]


@pytest.mark.unit
def test_same_repo_aging_pr_blocks_when_the_claim_names_no_paths() -> None:
    # Without claim paths disjointness cannot be shown, so the same-repo PR still blocks.
    fetch = _aging_fetch({"Repository_Management": [_old_pr(87)]}, {})

    block = rule.claim_blocked_by_aging_prs("codex", claim_repo="Repository_Management", fetch=fetch)

    assert block is not None
    assert block["aging_prs"][0]["overlapping_paths"] == []
    assert "Repository_Management" in block["message"]


@pytest.mark.unit
def test_unreadable_pr_files_count_as_overlap() -> None:
    def fetch(path: str) -> list[dict]:
        if path.endswith("/pulls?state=open&per_page=100") or path.endswith("/pulls?state=open"):
            return [_old_pr(87)]
        raise RuntimeError("files endpoint down")

    block = rule.claim_blocked_by_aging_prs(
        "codex", claim_repo="Repository_Management", claim_paths=["wood-biochar-methanol"], fetch=fetch
    )

    assert block is not None
