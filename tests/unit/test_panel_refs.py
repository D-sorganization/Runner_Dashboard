"""Unit tests for fetching GitHub issue/PR facts into a panel topic (#3313)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import gh_client
import pytest
from pydantic import ValidationError
from staff.panel_models import PanelCreateRequest
from staff.panel_refs import BODY_TRUNCATE_CHARS, fetch_refs_facts

EXPERTS = [{"name": name, "perspective": "review"} for name in ("Astra", "Argon", "Fable")]


@pytest.mark.unit
def test_refs_accepts_repo_hash_number_for_a_known_repository() -> None:
    request = PanelCreateRequest(topic="t", experts=EXPERTS, refs=["Runner_Dashboard#3313"])
    assert request.refs == ["Runner_Dashboard#3313"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "refs",
    [
        ["NotARepo#1"],
        ["Runner_Dashboard#abc"],
        ["Runner_Dashboard-3313"],
        ["Runner_Dashboard#1", "Runner_Dashboard#1"],
        [],
    ],
)
def test_refs_rejects_bad_syntax_unknown_repos_and_duplicates(refs: list[str]) -> None:
    with pytest.raises(ValidationError):
        PanelCreateRequest(topic="t", experts=EXPERTS, refs=refs)


def _issue(number: int = 3313, title: str = "feat(panels): refs", body: str = "Some facts.") -> dict[str, Any]:
    return {
        "number": number,
        "title": title,
        "state": "open",
        "html_url": f"https://github.com/D-sorganization/Runner_Dashboard/issues/{number}",
        "body": body,
    }


def _pull(number: int = 42) -> dict[str, Any]:
    return {**_issue(number), "pull_request": {"url": "..."}}


@pytest.mark.unit
async def test_fetches_one_ref_and_formats_title_state_url_and_body() -> None:
    with patch.object(gh_client, "get", AsyncMock(return_value=_issue())) as get:
        facts = await fetch_refs_facts(["Runner_Dashboard#3313"])
    get.assert_awaited_once_with("/repos/D-sorganization/Runner_Dashboard/issues/3313")
    assert "Runner_Dashboard#3313" in facts
    assert "feat(panels): refs" in facts
    assert "(open)" in facts
    assert "https://github.com/D-sorganization/Runner_Dashboard/issues/3313" in facts
    assert "Some facts." in facts


@pytest.mark.unit
async def test_labels_a_pull_request_distinctly_from_an_issue() -> None:
    with patch.object(gh_client, "get", AsyncMock(return_value=_pull())):
        facts = await fetch_refs_facts(["Runner_Dashboard#42"])
    assert "PR #42" in facts
    with patch.object(gh_client, "get", AsyncMock(return_value=_issue())):
        facts = await fetch_refs_facts(["Runner_Dashboard#3313"])
    assert "issue #3313" in facts


@pytest.mark.unit
async def test_fetches_multiple_refs_in_order() -> None:
    async def fake_get(path: str) -> dict[str, Any]:
        number = int(path.rsplit("/", 1)[-1])
        return _issue(number=number, title=f"issue {number}")

    with patch.object(gh_client, "get", AsyncMock(side_effect=fake_get)):
        facts = await fetch_refs_facts(["Runner_Dashboard#1", "Runner_Dashboard#2"])
    assert facts.index("issue 1") < facts.index("issue 2")


@pytest.mark.unit
async def test_truncates_a_long_body() -> None:
    with patch.object(gh_client, "get", AsyncMock(return_value=_issue(body="x" * 5000))):
        facts = await fetch_refs_facts(["Runner_Dashboard#3313"])
    assert "…(truncated)" in facts
    assert len(facts) < 5000
    assert "x" * BODY_TRUNCATE_CHARS in facts


@pytest.mark.unit
async def test_a_ref_github_has_no_issue_or_pr_for_raises_not_found() -> None:
    with patch.object(gh_client, "get", AsyncMock(side_effect=gh_client.GhNotFound("/repos/x/issues/999999"))):
        with pytest.raises(gh_client.GhNotFound):
            await fetch_refs_facts(["Runner_Dashboard#999999"])
