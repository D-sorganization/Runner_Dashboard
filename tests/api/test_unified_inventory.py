from __future__ import annotations

import copy
from typing import Any

import pytest
import unified_issue_inventory
from unified_issue_inventory import collapse, fetch_unified_issues


def github_issue(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "repository": "D-sorganization/runner-dashboard",
        "number": 240,
        "title": "GitHub title",
        "url": "https://github.com/D-sorganization/runner-dashboard/issues/240",
        "body": "",
        "author": "octocat",
        "assignees": ["hubot"],
        "labels": ["type:bug", "domain:backend"],
        "state": "open",
        "age_hours": 5.0,
        "taxonomy": {
            "type": "bug",
            "complexity": None,
            "effort": None,
            "judgement": None,
            "quick_win": False,
            "panel_review": False,
            "domains": ["backend"],
            "wave": None,
        },
        "agent_claim": None,
        "claim_expires_at": None,
        "linked_pr": None,
        "pickable": True,
        "pickable_blocked_by": [],
    }
    item.update(overrides)
    return item


def linear_issue(**overrides: Any) -> dict[str, Any]:
    linear: dict[str, Any] = {
        "id": "linear-id",
        "identifier": "ENG-123",
        "url": "https://linear.app/acme/issue/ENG-123/linear-title",
        "team_key": "ENG",
        "team_name": "Engineering",
        "priority": 2,
        "priority_label": "High",
        "estimate": 3,
        "state_name": "Started",
        "state_type": "started",
        "project_id": None,
        "cycle_number": None,
        "raw_label_names": ["Feature"],
        "github_attachments": [],
    }
    linear.update(overrides.pop("linear", {}))
    item: dict[str, Any] = {
        "repository": "",
        "number": None,
        "title": "Linear title",
        "url": "https://linear.app/acme/issue/ENG-123/linear-title",
        "author": "Ada Lovelace",
        "assignees": ["Grace Hopper"],
        "labels": ["type:task", "domain:backend", "effort:m"],
        "state": "open",
        "age_hours": 3.0,
        "taxonomy": {
            "type": "task",
            "complexity": None,
            "effort": "m",
            "judgement": None,
            "quick_win": False,
            "panel_review": False,
            "domains": ["backend"],
            "wave": None,
        },
        "agent_claim": None,
        "claim_expires_at": None,
        "linked_pr": None,
        "pickable": True,
        "pickable_blocked_by": [],
        "linear": linear,
        "sources": ["linear"],
    }
    item.update(overrides)
    return item


def test_collapse_match_via_attachment_url() -> None:
    github = github_issue()
    linear = linear_issue(linear={"github_attachments": [github["url"]]})

    items, collapsed = collapse([github], [linear])

    assert collapsed == 1
    assert items[0]["sources"] == ["github", "linear"]
    assert items[0]["github"] == {
        "repository": "D-sorganization/runner-dashboard",
        "number": 240,
        "url": github["url"],
    }


def test_collapse_match_via_body_linear_url() -> None:
    github = github_issue(body="Linked to: https://linear.app/acme/issue/ENG-123/linear-title")

    items, collapsed = collapse([github], [linear_issue()])

    assert collapsed == 1
    assert items[0]["linear"]["identifier"] == "ENG-123"


def test_collapse_attachment_match_takes_precedence_over_body_match() -> None:
    attached = github_issue(number=1, url="https://github.com/D-sorganization/runner-dashboard/issues/1")
    body_match = github_issue(
        number=2,
        url="https://github.com/D-sorganization/runner-dashboard/issues/2",
        body="Linked to: https://linear.app/acme/issue/ENG-123/linear-title",
    )
    linear = linear_issue(linear={"github_attachments": [attached["url"]]})

    items, collapsed = collapse([body_match, attached], [linear])

    assert collapsed == 1
    merged = next(item for item in items if item["sources"] == ["github", "linear"])
    assert merged["number"] == 1


def test_no_collapse_when_titles_match_but_no_url_signal() -> None:
    items, collapsed = collapse([github_issue(title="Same")], [linear_issue(title="Same")])

    assert collapsed == 0
    assert [item["sources"] for item in items] == [["linear"], ["github"]]


def test_linear_only_item_keeps_canonical_shape_with_github_null() -> None:
    items, collapsed = collapse([], [linear_issue()])

    assert collapsed == 0
    assert items[0]["github"] is None
    assert items[0]["primary_source"] == "linear"
    assert items[0]["linear"]["identifier"] == "ENG-123"


def test_github_only_item_keeps_canonical_shape_with_linear_null() -> None:
    items, collapsed = collapse([github_issue()], [])

    assert collapsed == 0
    assert items[0]["linear"] is None
    assert items[0]["primary_source"] == "github"
    assert items[0]["github"]["number"] == 240


def test_merged_repository_comes_from_github() -> None:
    items, _ = collapse([github_issue(repository="D-sorganization/runner-dashboard")], [_matched_linear()])

    assert items[0]["repository"] == "D-sorganization/runner-dashboard"


def test_merged_title_comes_from_linear_when_prefer_source_linear() -> None:
    items, _ = collapse([github_issue(title="GitHub")], [_matched_linear(title="Linear")])

    assert items[0]["title"] == "Linear"
    assert items[0]["primary_source"] == "linear"


def test_merged_title_comes_from_github_when_prefer_source_github() -> None:
    items, _ = collapse([github_issue(title="GitHub")], [_matched_linear(title="Linear")], prefer_source="github")

    assert items[0]["title"] == "GitHub"
    assert items[0]["primary_source"] == "github"


def test_merged_labels_union_deduped() -> None:
    github = github_issue(labels=["domain:backend", "type:bug"])
    linear = _matched_linear(labels=["domain:backend", "effort:m"])

    items, _ = collapse([github], [linear])

    assert items[0]["labels"] == ["domain:backend", "type:bug", "effort:m"]


def test_merged_state_closed_if_either_side_closed() -> None:
    items, _ = collapse([github_issue(state="open")], [_matched_linear(state="closed")])

    assert items[0]["state"] == "closed"


def test_merged_age_hours_is_minimum() -> None:
    items, _ = collapse([github_issue(age_hours=7.0)], [_matched_linear(age_hours=2.5)])

    assert items[0]["age_hours"] == 2.5


def test_merged_agent_claim_always_from_github() -> None:
    github = github_issue(agent_claim="codex", claim_expires_at="2026-04-28T23:00:00Z")
    linear = _matched_linear(linear={"agent_claim": "linear"})

    items, _ = collapse([github], [linear])

    assert items[0]["agent_claim"] == "codex"
    assert items[0]["claim_expires_at"] == "2026-04-28T23:00:00Z"


def test_merged_pickable_is_and() -> None:
    github = github_issue(pickable=False, pickable_blocked_by=["active claim: codex"])
    linear = _matched_linear(pickable=True)

    items, _ = collapse([github], [linear])

    assert items[0]["pickable"] is False
    assert items[0]["pickable_blocked_by"] == ["active claim: codex"]


def test_merged_pickable_blocked_by_union() -> None:
    github = github_issue(pickable=False, pickable_blocked_by=["active claim: codex", "has linked open PR"])
    linear = _matched_linear(pickable=False, pickable_blocked_by=["has linked open PR", "state != open"])

    items, _ = collapse([github], [linear])

    assert items[0]["pickable_blocked_by"] == ["active claim: codex", "has linked open PR", "state != open"]


def test_taxonomy_re_derived_from_merged_labels() -> None:
    github = github_issue(labels=["type:bug"])
    linear = _matched_linear(labels=["domain:backend", "effort:m"], taxonomy={"type": "task", "domains": []})

    items, _ = collapse([github], [linear])

    assert items[0]["taxonomy"]["type"] == "bug"
    assert items[0]["taxonomy"]["effort"] == "m"
    assert items[0]["taxonomy"]["domains"] == ["backend"]


@pytest.mark.asyncio
async def test_stats_counts_correct(monkeypatch: pytest.MonkeyPatch) -> None:
    github = github_issue(number=1, url="https://github.com/D-sorganization/runner-dashboard/issues/1")
    linear = linear_issue(linear={"github_attachments": [github["url"]]})

    async def fake_github_fetch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"items": [github, github_issue(number=2)], "errors": ["github warning"]}

    async def fake_linear_fetch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"items": [linear, linear_issue(linear={"identifier": "ENG-999"})], "errors": ["linear warning"]}

    monkeypatch.setattr(unified_issue_inventory.issue_inventory, "fetch_all_issues", fake_github_fetch)
    monkeypatch.setattr(unified_issue_inventory.linear_inventory, "fetch_all_issues", fake_linear_fetch)

    result = await fetch_unified_issues(github_repos=["org/repo"], linear_config={}, linear_client=object())  # type: ignore[arg-type]

    assert result["stats"] == {
        "github_total": 2,
        "linear_total": 2,
        "collapsed": 1,
        "linear_only": 1,
        "github_only": 1,
        "unified_total": 3,
    }
    assert result["errors"] == ["github warning", "linear warning"]


def test_collapse_pure_function_does_not_mutate_inputs() -> None:
    github_items = [github_issue()]
    linear_items = [_matched_linear()]
    original_github = copy.deepcopy(github_items)
    original_linear = copy.deepcopy(linear_items)

    collapse(github_items, linear_items)

    assert github_items == original_github
    assert linear_items == original_linear


def test_match_url_normalization_handles_trailing_slash() -> None:
    github = github_issue(number=42, url="https://github.com/D-sorganization/runner-dashboard/issues/42")
    linear = linear_issue(
        linear={"github_attachments": ["https://github.com/D-sorganization/runner-dashboard/issues/42/#comment-X"]}
    )

    items, collapsed = collapse([github], [linear])

    assert collapsed == 1
    assert items[0]["number"] == 42


def test_match_url_case_insensitive() -> None:
    github = github_issue(url="https://Github.com/D-sorganization/Runner-Dashboard/issues/42")
    linear = linear_issue(
        linear={"github_attachments": ["https://github.com/d-sorganization/runner-dashboard/issues/42"]}
    )

    _items, collapsed = collapse([github], [linear])

    assert collapsed == 1


def test_two_linear_items_pointing_to_same_github_uses_first() -> None:
    github = github_issue()
    first = linear_issue(title="first", linear={"identifier": "ENG-1", "github_attachments": [github["url"]]})
    second = linear_issue(title="second", linear={"identifier": "ENG-2", "github_attachments": [github["url"]]})

    items, collapsed = collapse([github], [first, second])

    assert collapsed == 1
    assert [item["title"] for item in items] == ["first", "second"]
    assert items[1]["github"] is None


def test_one_linear_attaches_two_github_urls() -> None:
    issue = github_issue(number=42, url="https://github.com/D-sorganization/runner-dashboard/issues/42")
    linear = linear_issue(
        linear={
            "github_attachments": [
                "https://github.com/D-sorganization/runner-dashboard/pull/99",
                "https://github.com/D-sorganization/runner-dashboard/issues/42",
            ]
        }
    )

    items, collapsed = collapse([issue], [linear])

    assert collapsed == 1
    assert items[0]["number"] == 42
    assert items[0]["linked_pr"] is None


@pytest.mark.asyncio
async def test_fetch_unified_issues_returns_github_only_when_linear_client_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github = github_issue()

    async def fake_github_fetch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"items": [github], "errors": []}

    monkeypatch.setattr(unified_issue_inventory.issue_inventory, "fetch_all_issues", fake_github_fetch)

    result = await fetch_unified_issues(github_repos=["org/repo"], linear_config={}, linear_client=None)

    assert result["items"] == [
        {
            **github,
            "sources": ["github"],
            "primary_source": "github",
            "linear": None,
            "github": {
                "repository": "D-sorganization/runner-dashboard",
                "number": 240,
                "url": github["url"],
            },
        }
    ]
    assert result["stats"]["github_total"] == 1
    assert result["stats"]["unified_total"] == 1


def _matched_linear(**overrides: Any) -> dict[str, Any]:
    github_url = "https://github.com/D-sorganization/runner-dashboard/issues/240"
    linear_overrides = overrides.pop("linear", {})
    attachments = linear_overrides.setdefault("github_attachments", [github_url])
    if not attachments:
        linear_overrides["github_attachments"] = [github_url]
    return linear_issue(linear=linear_overrides, **overrides)
