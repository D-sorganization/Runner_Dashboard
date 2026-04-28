from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from linear_inventory import _cache, fetch_all_issues, fetch_workspace_issues


class FakeLinearClient:
    def __init__(self, responses: dict[str, list[dict[str, Any]] | Exception]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def fetch_issues(
        self,
        workspace_id: str,
        *,
        team_keys: list[str] | None = None,
        state_types: list[str] | None = None,
        updated_after: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {
                "workspace_id": workspace_id,
                "team_keys": team_keys,
                "state_types": state_types,
                "updated_after": updated_after,
                "limit": limit,
            }
        )
        response = self.responses[workspace_id]
        if isinstance(response, Exception):
            raise response
        return response[:limit]


def base_mapping() -> dict[str, Any]:
    return {
        "priority": {
            "0": [],
            "1": ["complexity:trivial", "quick-win"],
            "2": ["complexity:routine"],
            "3": ["complexity:routine"],
            "4": ["complexity:complex"],
        },
        "estimate": {"1": ["effort:xs"], "2": ["effort:s"], "3": ["effort:m"], "5": ["effort:l"]},
        "state_type": {
            "triage": ["judgement:design"],
            "backlog": [],
            "unstarted": [],
            "started": [],
            "completed": [],
            "canceled": [],
        },
        "label_aliases": {"Feature": ["type:task"], "Bug": ["type:bug"]},
        "label_passthrough_prefixes": ["type:", "domain:", "wave:", "complexity:", "effort:", "judgement:"],
        "default_judgement": "objective",
    }


def workspace(workspace_id: str = "linear-main", *, teams: list[str] | None = None) -> dict[str, Any]:
    return {"id": workspace_id, "teams": teams or ["ENG"], "mapping": "default"}


def config(*workspaces: dict[str, Any]) -> dict[str, Any]:
    return {"workspaces": list(workspaces), "mappings": {"default": base_mapping()}}


def created_at(hours_ago: float = 2.0) -> str:
    created = dt.datetime.now(tz=dt.UTC) - dt.timedelta(hours=hours_ago)
    return created.isoformat().replace("+00:00", "Z")


def labels(*names: str) -> dict[str, list[dict[str, str]]]:
    return {"nodes": [{"name": name} for name in names]}


def linear_issue(**overrides: Any) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "id": "issue-id",
        "identifier": "ENG-123",
        "title": "Build Linear inventory",
        "url": "https://linear.app/acme/issue/ENG-123/build-linear-inventory",
        "creator": {"displayName": "Ada Lovelace", "email": "ada@example.test"},
        "assignee": {"displayName": "Grace Hopper", "email": "grace@example.test"},
        "priority": 2,
        "priorityLabel": "High",
        "estimate": 3,
        "createdAt": created_at(),
        "state": {"name": "Started", "type": "started"},
        "team": {"key": "ENG", "name": "Engineering"},
        "project": {"id": "project-id", "name": "Linear Integration"},
        "cycle": {"number": 7},
        "labels": labels("Feature", "domain:backend"),
        "attachments": {"nodes": []},
    }
    issue.update(overrides)
    return issue


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    _cache.clear()


@pytest.mark.asyncio
async def test_fetch_workspace_issues_normalizes_to_canonical_shape() -> None:
    client = FakeLinearClient({"linear-main": [linear_issue()]})

    items, error = await fetch_workspace_issues(workspace(), base_mapping(), client)  # type: ignore[arg-type]

    assert error is None
    assert items[0] == {
        "repository": "",
        "number": None,
        "title": "Build Linear inventory",
        "url": "https://linear.app/acme/issue/ENG-123/build-linear-inventory",
        "author": "Ada Lovelace",
        "assignees": ["Grace Hopper"],
        "labels": ["complexity:routine", "effort:m", "type:task", "domain:backend", "judgement:objective"],
        "state": "open",
        "age_hours": items[0]["age_hours"],
        "taxonomy": {
            "type": "task",
            "complexity": "routine",
            "effort": "m",
            "judgement": "objective",
            "quick_win": False,
            "panel_review": False,
            "domains": ["backend"],
            "wave": None,
        },
        "agent_claim": None,
        "claim_expires_at": None,
        "linked_pr": None,
        "pickable": False,
        "pickable_blocked_by": [],
        "linear": {
            "id": "issue-id",
            "identifier": "ENG-123",
            "url": "https://linear.app/acme/issue/ENG-123/build-linear-inventory",
            "team_key": "ENG",
            "team_name": "Engineering",
            "priority": 2,
            "priority_label": "High",
            "estimate": 3,
            "state_name": "Started",
            "state_type": "started",
            "project_id": "project-id",
            "cycle_number": 7,
            "raw_label_names": ["Feature", "domain:backend"],
            "github_attachments": [],
        },
        "sources": ["linear"],
    }


@pytest.mark.asyncio
async def test_fetch_workspace_issues_extracts_github_attachment_url() -> None:
    issue = linear_issue(
        attachments={
            "nodes": [
                {"url": "https://example.test/not-github"},
                {"url": "https://github.com/D-sorganization/runner-dashboard/issues/239?from=linear"},
            ]
        }
    )
    client = FakeLinearClient({"linear-main": [issue]})

    items, _ = await fetch_workspace_issues(workspace(), base_mapping(), client)  # type: ignore[arg-type]

    assert items[0]["repository"] == "D-sorganization/runner-dashboard"
    assert items[0]["number"] == 239
    assert items[0]["linear"]["github_attachments"] == [
        "https://github.com/D-sorganization/runner-dashboard/issues/239"
    ]


@pytest.mark.asyncio
async def test_fetch_workspace_issues_no_github_attachment_repository_empty() -> None:
    client = FakeLinearClient({"linear-main": [linear_issue()]})

    items, _ = await fetch_workspace_issues(workspace(), base_mapping(), client)  # type: ignore[arg-type]

    assert items[0]["repository"] == ""
    assert items[0]["number"] is None


@pytest.mark.asyncio
async def test_state_started_maps_to_open() -> None:
    client = FakeLinearClient({"linear-main": [linear_issue(state={"name": "Started", "type": "started"})]})

    items, _ = await fetch_workspace_issues(workspace(), base_mapping(), client)  # type: ignore[arg-type]

    assert items[0]["state"] == "open"


@pytest.mark.asyncio
async def test_state_completed_maps_to_closed() -> None:
    client = FakeLinearClient({"linear-main": [linear_issue(state={"name": "Done", "type": "completed"})]})

    items, _ = await fetch_workspace_issues(workspace(), base_mapping(), client)  # type: ignore[arg-type]

    assert items[0]["state"] == "closed"


@pytest.mark.asyncio
async def test_fetch_all_issues_aggregates_across_workspaces() -> None:
    client = FakeLinearClient({"one": [linear_issue(identifier="ONE-1")], "two": [linear_issue(identifier="TWO-1")]})

    result = await fetch_all_issues(config(workspace("one"), workspace("two")), client)  # type: ignore[arg-type]

    assert [item["linear"]["identifier"] for item in result["items"]] == ["ONE-1", "TWO-1"]
    assert result["errors"] == []


@pytest.mark.asyncio
async def test_fetch_all_issues_one_workspace_failing_does_not_block_others() -> None:
    client = FakeLinearClient({"one": [linear_issue(identifier="ONE-1")], "two": RuntimeError("bad key")})

    result = await fetch_all_issues(config(workspace("one"), workspace("two")), client)  # type: ignore[arg-type]

    assert [item["linear"]["identifier"] for item in result["items"]] == ["ONE-1"]
    assert result["errors"] == ["two: bad key"]


@pytest.mark.asyncio
async def test_fetch_all_issues_pickable_only_filter() -> None:
    client = FakeLinearClient(
        {
            "linear-main": [
                linear_issue(identifier="ENG-1", state={"name": "Started", "type": "started"}),
                linear_issue(identifier="ENG-2", state={"name": "Triage", "type": "triage"}),
            ]
        }
    )

    result = await fetch_all_issues(config(workspace()), client, pickable_only=True)  # type: ignore[arg-type]

    assert [item["linear"]["identifier"] for item in result["items"]] == ["ENG-1"]


@pytest.mark.asyncio
async def test_fetch_all_issues_caches_results_for_30_seconds() -> None:
    client = FakeLinearClient({"linear-main": [linear_issue(identifier="ENG-1")]})

    first = await fetch_all_issues(config(workspace()), client)  # type: ignore[arg-type]
    client.responses["linear-main"] = [linear_issue(identifier="ENG-2")]
    second = await fetch_all_issues(config(workspace()), client)  # type: ignore[arg-type]

    assert [item["linear"]["identifier"] for item in first["items"]] == ["ENG-1"]
    assert [item["linear"]["identifier"] for item in second["items"]] == ["ENG-1"]
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_fetch_all_issues_filters_by_complexity_label() -> None:
    client = FakeLinearClient(
        {"linear-main": [linear_issue(identifier="ENG-1", priority=2), linear_issue(identifier="ENG-2", priority=4)]}
    )

    result = await fetch_all_issues(config(workspace()), client, complexity=["complex"])  # type: ignore[arg-type]

    assert [item["linear"]["identifier"] for item in result["items"]] == ["ENG-2"]


@pytest.mark.asyncio
async def test_fetch_all_issues_filters_by_effort_label() -> None:
    client = FakeLinearClient(
        {"linear-main": [linear_issue(identifier="ENG-1", estimate=3), linear_issue(identifier="ENG-2", estimate=5)]}
    )

    result = await fetch_all_issues(config(workspace()), client, effort=["l"])  # type: ignore[arg-type]

    assert [item["linear"]["identifier"] for item in result["items"]] == ["ENG-2"]


@pytest.mark.asyncio
async def test_pickability_blocks_triage_state() -> None:
    client = FakeLinearClient({"linear-main": [linear_issue(state={"name": "Triage", "type": "triage"})]})

    result = await fetch_all_issues(config(workspace()), client)  # type: ignore[arg-type]

    assert result["items"][0]["pickable"] is False
    assert result["items"][0]["pickable_blocked_by"] == ["judgement:design requires panel review"]


@pytest.mark.asyncio
async def test_pickability_open_no_judgement_block_pickable_true() -> None:
    client = FakeLinearClient({"linear-main": [linear_issue()]})

    result = await fetch_all_issues(config(workspace()), client)  # type: ignore[arg-type]

    assert result["items"][0]["pickable"] is True
    assert result["items"][0]["pickable_blocked_by"] == []


@pytest.mark.asyncio
async def test_age_hours_uses_linear_created_at() -> None:
    client = FakeLinearClient({"linear-main": [linear_issue(createdAt=created_at(6.0))]})

    items, _ = await fetch_workspace_issues(workspace(), base_mapping(), client)  # type: ignore[arg-type]

    assert 5.9 <= items[0]["age_hours"] <= 6.1


@pytest.mark.asyncio
async def test_team_keys_filter_overrides_workspace_default() -> None:
    client = FakeLinearClient({"linear-main": [linear_issue()]})

    await fetch_workspace_issues(workspace(teams=["OPS"]), base_mapping(), client, team_keys=["ENG"])  # type: ignore[arg-type]

    assert client.calls[0]["team_keys"] == ["ENG"]


@pytest.mark.asyncio
async def test_workspace_with_teams_wildcard_passes_no_team_filter() -> None:
    client = FakeLinearClient({"linear-main": [linear_issue()]})

    await fetch_workspace_issues(workspace(teams=["*"]), base_mapping(), client)  # type: ignore[arg-type]

    assert client.calls[0]["team_keys"] is None


@pytest.mark.asyncio
async def test_pagination_continues_until_limit() -> None:
    client = FakeLinearClient({"linear-main": [linear_issue(identifier=f"ENG-{index}") for index in range(10)]})

    items, _ = await fetch_workspace_issues(workspace(), base_mapping(), client, limit=3)  # type: ignore[arg-type]

    assert [item["linear"]["identifier"] for item in items] == ["ENG-0", "ENG-1", "ENG-2"]
    assert client.calls[0]["limit"] == 3


@pytest.mark.asyncio
async def test_returned_taxonomy_dict_does_not_contain_derived_labels_or_source_signals() -> None:
    client = FakeLinearClient({"linear-main": [linear_issue()]})

    items, _ = await fetch_workspace_issues(workspace(), base_mapping(), client)  # type: ignore[arg-type]

    assert "derived_labels" not in items[0]["taxonomy"]
    assert "source_signals" not in items[0]["taxonomy"]
