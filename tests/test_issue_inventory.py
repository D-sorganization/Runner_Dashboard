"""Unit tests for backend/issue_inventory.py (issue #81)."""

from __future__ import annotations  # noqa: E402

import sys  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
UTC = timezone.utc
from pathlib import Path  # noqa: E402
from unittest.mock import AsyncMock, patch  # noqa: E402

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import issue_inventory  # noqa: E402  # noqa: E402

# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_issue(
    number: int = 1,
    title: str = "Test issue",
    author: str = "alice",
    labels: list[str] | None = None,
    assignees: list[str] | None = None,
    body: str = "",
    created_at: str | None = None,
    state: str = "open",
) -> dict:
    if created_at is None:
        created_at = datetime.now(tz=UTC).isoformat()
    return {
        "number": number,
        "title": title,
        "html_url": f"https://github.com/org/repo/issues/{number}",
        "user": {"login": author},
        "labels": [{"name": lbl} for lbl in (labels or [])],
        "assignees": [{"login": a} for a in (assignees or [])],
        "body": body,
        "created_at": created_at,
        "state": state,
    }


# ─── parse_taxonomy ───────────────────────────────────────────────────────────


class TestParseTaxonomy:
    def test_empty_labels(self) -> None:
        result = issue_inventory.parse_taxonomy([])
        assert result["type"] is None
        assert result["complexity"] is None
        assert result["effort"] is None
        assert result["judgement"] is None
        assert result["quick_win"] is False
        assert result["panel_review"] is False
        assert result["domains"] == []
        assert result["wave"] is None

    def test_type_label(self) -> None:
        result = issue_inventory.parse_taxonomy(["type:task"])
        assert result["type"] == "task"

    def test_complexity_label(self) -> None:
        result = issue_inventory.parse_taxonomy(["complexity:routine"])
        assert result["complexity"] == "routine"

    def test_effort_label(self) -> None:
        result = issue_inventory.parse_taxonomy(["effort:m"])
        assert result["effort"] == "m"

    def test_judgement_label(self) -> None:
        result = issue_inventory.parse_taxonomy(["judgement:objective"])
        assert result["judgement"] == "objective"

    def test_quick_win_flag(self) -> None:
        result = issue_inventory.parse_taxonomy(["quick-win"])
        assert result["quick_win"] is True

    def test_panel_review_flag(self) -> None:
        result = issue_inventory.parse_taxonomy(["panel-review"])
        assert result["panel_review"] is True

    def test_wave_integer(self) -> None:
        result = issue_inventory.parse_taxonomy(["wave:2"])
        assert result["wave"] == 2

    def test_domain_labels(self) -> None:
        result = issue_inventory.parse_taxonomy(["domain:backend", "domain:ci"])
        assert set(result["domains"]) == {"backend", "ci"}

    def test_full_taxonomy(self) -> None:
        labels = [
            "type:task",
            "complexity:trivial",
            "effort:s",
            "judgement:objective",
            "quick-win",
            "wave:1",
            "domain:backend",
        ]
        result = issue_inventory.parse_taxonomy(labels)
        assert result["type"] == "task"
        assert result["complexity"] == "trivial"
        assert result["effort"] == "s"
        assert result["judgement"] == "objective"
        assert result["quick_win"] is True
        assert result["wave"] == 1
        assert "backend" in result["domains"]

    def test_unknown_labels_ignored(self) -> None:
        result = issue_inventory.parse_taxonomy(["bug", "needs-triage"])
        assert result["type"] is None
        assert result["complexity"] is None

    def test_claim_label_not_in_taxonomy(self) -> None:
        result = issue_inventory.parse_taxonomy(["claim:claude"])
        assert result["type"] is None

    def test_wave_non_integer_stored_as_string(self) -> None:
        result = issue_inventory.parse_taxonomy(["wave:alpha"])
        assert result["wave"] == "alpha"


# ─── is_pickable ──────────────────────────────────────────────────────────────


class TestIsPickable:
    def _base_item(self, **kwargs) -> dict:
        item = {
            "state": "open",
            "agent_claim": None,
            "taxonomy": {
                "type": "task",
                "complexity": "routine",
                "effort": "m",
                "judgement": "objective",
                "quick_win": False,
                "panel_review": False,
                "domains": [],
                "wave": None,
            },
            "linked_pr": None,
        }
        item.update(kwargs)
        return item

    def test_open_no_claim_no_pr_is_pickable(self) -> None:
        item = self._base_item()
        pickable, blocked = issue_inventory.is_pickable(item, has_open_pr=False)
        assert pickable is True
        assert blocked == []

    def test_closed_issue_not_pickable(self) -> None:
        item = self._base_item(state="closed")
        pickable, blocked = issue_inventory.is_pickable(item, has_open_pr=False)
        assert pickable is False
        assert any("state" in r for r in blocked)

    def test_has_linked_pr_not_pickable(self) -> None:
        item = self._base_item()
        pickable, blocked = issue_inventory.is_pickable(item, has_open_pr=True)
        assert pickable is False
        assert any("PR" in r for r in blocked)

    def test_active_claim_not_pickable(self) -> None:
        item = self._base_item(agent_claim="claude")
        pickable, blocked = issue_inventory.is_pickable(item, has_open_pr=False)
        assert pickable is False
        assert any("claim" in r for r in blocked)

    def test_design_judgement_not_pickable(self) -> None:
        item = self._base_item()
        item["taxonomy"]["judgement"] = "design"
        pickable, blocked = issue_inventory.is_pickable(item, has_open_pr=False)
        assert pickable is False
        assert any("design" in r for r in blocked)

    def test_contested_judgement_not_pickable(self) -> None:
        item = self._base_item()
        item["taxonomy"]["judgement"] = "contested"
        pickable, blocked = issue_inventory.is_pickable(item, has_open_pr=False)
        assert pickable is False

    def test_multiple_blockers_reported(self) -> None:
        item = self._base_item(state="closed", agent_claim="jules")
        item["taxonomy"]["judgement"] = "design"
        pickable, blocked = issue_inventory.is_pickable(item, has_open_pr=True)
        assert pickable is False
        assert len(blocked) >= 2


# ─── linked_pr resolution ────────────────────────────────────────────────────


class TestLinkedPr:
    """linked_pr is set by the caller (fetch_all_issues) based on PR data.
    Here we verify that normalise_issue defaults it to None."""

    def test_linked_pr_defaults_to_none(self) -> None:
        raw = _make_issue(1, "Test")
        result = issue_inventory._normalise_issue(raw, "org/repo")
        assert result["linked_pr"] is None


# ─── age_hours ────────────────────────────────────────────────────────────────


class TestAgeHours:
    def test_recent_issue(self) -> None:
        created = datetime.now(tz=UTC) - timedelta(hours=3)
        age = issue_inventory._age_hours(created.isoformat())
        assert 2.9 <= age <= 3.1

    def test_invalid_returns_zero(self) -> None:
        assert issue_inventory._age_hours("bad") == 0.0

    def test_z_suffix(self) -> None:
        created = datetime.now(tz=UTC) - timedelta(hours=1)
        ts = created.strftime("%Y-%m-%dT%H:%M:%SZ")
        age = issue_inventory._age_hours(ts)
        assert 0.9 <= age <= 1.1


# ─── fetch_repo_issues ────────────────────────────────────────────────────────


class TestFetchRepoIssues:
    @pytest.mark.asyncio
    async def test_gh_failure_returns_error(self) -> None:
        with patch.object(
            issue_inventory,
            "_run_gh",
            new=AsyncMock(return_value=(1, "", "auth error")),
        ):
            items, err = await issue_inventory.fetch_repo_issues("org/repo")
        assert items == []
        assert err is not None
        assert "org/repo" in err

    @pytest.mark.asyncio
    async def test_prs_filtered_out(self) -> None:
        """Issues endpoint returns PRs too; they must be excluded."""
        import json

        raw = [
            _make_issue(1, "Real issue"),
            {**_make_issue(2, "A PR"), "pull_request": {"url": "..."}},
        ]
        with patch.object(
            issue_inventory,
            "_run_gh",
            new=AsyncMock(return_value=(0, json.dumps(raw), "")),
        ):
            items, err = await issue_inventory.fetch_repo_issues("org/repo")
        assert err is None
        assert len(items) == 1
        assert items[0]["number"] == 1

    @pytest.mark.asyncio
    async def test_json_error_returns_error(self) -> None:
        with patch.object(
            issue_inventory, "_run_gh", new=AsyncMock(return_value=(0, "oops", ""))
        ):
            items, err = await issue_inventory.fetch_repo_issues("org/repo")
        assert items == []
        assert err is not None


# ─── fetch_all_issues ────────────────────────────────────────────────────────


class TestFetchAllIssues:
    @pytest.mark.asyncio
    async def test_per_repo_error_captured(self) -> None:
        async def fake_fetch(
            full_name: str, state: str = "open"
        ) -> tuple[list, str | None]:
            if full_name == "org/bad":
                return [], "org/bad: gh exit 1: not found"
            raw = _make_issue(1, "Good issue")
            return [issue_inventory._normalise_issue(raw, full_name)], None

        with patch.object(issue_inventory, "fetch_repo_issues", side_effect=fake_fetch):
            result = await issue_inventory.fetch_all_issues(["org/good", "org/bad"])

        assert any("org/bad" in e for e in result["errors"])
        assert len(result["items"]) == 1

    @pytest.mark.asyncio
    async def test_pickable_only_filter(self) -> None:
        raw_pickable = _make_issue(
            1, "Pickable", labels=["type:task", "judgement:objective"]
        )
        raw_blocked = _make_issue(2, "Contested", labels=["judgement:contested"])

        async def fake_fetch(
            full_name: str, state: str = "open"
        ) -> tuple[list, str | None]:
            return [
                issue_inventory._normalise_issue(raw_pickable, full_name),
                issue_inventory._normalise_issue(raw_blocked, full_name),
            ], None

        with patch.object(issue_inventory, "fetch_repo_issues", side_effect=fake_fetch):
            result = await issue_inventory.fetch_all_issues(
                ["org/repo"], pickable_only=True
            )

        assert all(i["pickable"] for i in result["items"])

    @pytest.mark.asyncio
    async def test_complexity_filter(self) -> None:
        issues = [
            _make_issue(1, "Trivial", labels=["complexity:trivial"]),
            _make_issue(2, "Routine", labels=["complexity:routine"]),
        ]

        async def fake_fetch(
            full_name: str, state: str = "open"
        ) -> tuple[list, str | None]:
            return [
                issue_inventory._normalise_issue(i, full_name) for i in issues
            ], None

        with patch.object(issue_inventory, "fetch_repo_issues", side_effect=fake_fetch):
            result = await issue_inventory.fetch_all_issues(
                ["org/repo"], complexity=["trivial"]
            )

        assert result["items"][0]["taxonomy"]["complexity"] == "trivial"
        assert len(result["items"]) == 1

    @pytest.mark.asyncio
    async def test_limit_respected(self) -> None:
        issues = [_make_issue(i, f"Issue {i}") for i in range(20)]

        async def fake_fetch(
            full_name: str, state: str = "open"
        ) -> tuple[list, str | None]:
            return [
                issue_inventory._normalise_issue(i, full_name) for i in issues
            ], None

        with patch.object(issue_inventory, "fetch_repo_issues", side_effect=fake_fetch):
            result = await issue_inventory.fetch_all_issues(["org/repo"], limit=5)

        assert len(result["items"]) == 5

    @pytest.mark.asyncio
    async def test_assignee_filter(self) -> None:
        issues = [
            _make_issue(1, "Alice issue", assignees=["alice"]),
            _make_issue(2, "Bob issue", assignees=["bob"]),
        ]

        async def fake_fetch(
            full_name: str, state: str = "open"
        ) -> tuple[list, str | None]:
            return [
                issue_inventory._normalise_issue(i, full_name) for i in issues
            ], None

        with patch.object(issue_inventory, "fetch_repo_issues", side_effect=fake_fetch):
            result = await issue_inventory.fetch_all_issues(
                ["org/repo"], assignee="alice"
            )

        assert len(result["items"]) == 1
        assert "alice" in result["items"][0]["assignees"]
